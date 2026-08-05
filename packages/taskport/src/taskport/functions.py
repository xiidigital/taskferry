"""Naming Python callables so a worker in another process can find them.

A remote task is a *name*, not an object. Taskport's portable form is::

    package.module:function_name

Chosen because it is unambiguous (a colon separates module from attribute, so
``a.b.c`` never has to be guessed at), it is already the convention used by
entry points, uvicorn and gunicorn, and it survives any transport as a plain
string.

Taskport never pickles a callable. Pickling ties the payload to one interpreter
version and one code layout, and unpickling attacker-controlled bytes is remote
code execution. A reference plus JSON arguments is portable and inspectable.

Security
--------

Resolving a reference means importing a module and calling an attribute of it.
If task names arrive from an untrusted queue, an unrestricted resolver is an RCE
primitive. :class:`FunctionRegistry` therefore supports two controls:

* **explicit registration** — resolve only what was registered;
* **an import allowlist** — permit dynamic import only from named packages.

The default (``allow_import=True``, empty allowlist) is convenient for
development. Production deployments should set an allowlist; see
``docs/security.md``.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from .errors import FunctionResolutionError


@dataclass(frozen=True, slots=True)
class FunctionRef:
    """A portable reference to a module-level callable."""

    module: str
    qualname: str

    def __post_init__(self) -> None:
        if not self.module or not self.qualname:
            raise ValueError("FunctionRef needs both a module and a qualname")

    def __str__(self) -> str:
        return self.path

    @property
    def path(self) -> str:
        """The portable ``package.module:function`` string."""
        return f"{self.module}:{self.qualname}"

    @classmethod
    def parse(cls, path: str) -> FunctionRef:
        """Parse ``"package.module:function"``.

        Legacy dotted form (``"package.module.function"``) is accepted so 0.1
        configuration keeps working; the colon form is unambiguous and preferred.
        """
        module, sep, qualname = path.partition(":")
        if not sep:
            module, _, qualname = path.rpartition(".")
        if not module or not qualname:
            raise FunctionResolutionError(
                f"{path!r} is not a valid function reference; expected 'package.module:function'"
            )
        return cls(module=module, qualname=qualname)

    @classmethod
    def from_callable(cls, func: Callable[..., Any]) -> FunctionRef:
        """Derive a reference from a callable.

        Rejects anything a worker in another process could not reconstruct:
        lambdas, closures, locals, ``__main__``, and — less obviously — callables
        that carry **state**. Failing here is far kinder than failing on a worker.

        The stateful cases are the subtle ones, and they are the shape a library
        embedding Taskport usually reaches for. A configured object like::

            processor = Processor(factor=3)
            runtime.tasks.submit(processor.run, 14)

        has a perfectly good importable name — ``mypkg:Processor.run`` — so a
        naive check accepts it. But only the *name* travels: the worker imports
        the plain function, ``factor`` is gone, and the call fails with a
        confusing ``TypeError`` about a missing argument, on the worker, at 3am.
        The same is true of a callable instance, whose name resolves to its class.

        Both are refused here, pointing at the way through: one importable
        dispatcher, with the identity as JSON data. That is what
        ``taskport_django.execute:run_task`` does for Django's Task objects, and
        it is the pattern any embedder with configured units of work needs.
        """
        method_self = getattr(func, "__self__", None)
        if method_self is not None and not isinstance(method_self, type):
            # A bound method of an *instance*. A classmethod (``__self__`` is the
            # class) re-binds correctly on import and is deliberately allowed.
            owner = type(method_self).__name__
            raise FunctionResolutionError(
                f"{func!r} is a method bound to a {owner} instance. Only its name would "
                f"reach the worker, so the instance's state would be lost and the call "
                f"would fail there rather than here. Submit a module-level function, or "
                f"route through one importable dispatcher that rebuilds the {owner} from "
                f"JSON arguments."
            )
        if (
            method_self is None  # a classmethod already passed the check above
            and not isinstance(func, type)
            and not inspect.isfunction(func)
            and not inspect.isbuiltin(func)
            and callable(func)
        ):
            # A callable *instance*: its ``__qualname__`` names the class, so the
            # worker would import the class and construct a new object with the
            # task's arguments — silently the wrong thing.
            raise FunctionResolutionError(
                f"{func!r} is a callable {type(func).__name__} instance, not a function. "
                f"Its name resolves to the class, so a worker would construct a new object "
                f"rather than use this one. Submit a module-level function, or route "
                f"through one importable dispatcher."
            )

        module = getattr(func, "__module__", None)
        qualname = getattr(func, "__qualname__", None)
        if not module or not qualname:
            raise FunctionResolutionError(
                f"{func!r} has no importable name; only module-level functions can be "
                "referenced remotely"
            )
        if "<locals>" in qualname or qualname == "<lambda>":
            raise FunctionResolutionError(
                f"{qualname!r} is defined inside another scope and cannot be resolved by a "
                "remote worker; move it to module level"
            )
        if module == "__main__":
            raise FunctionResolutionError(
                f"{qualname!r} is defined in __main__, which resolves to a different module "
                "in a worker process; move it into an importable module"
            )
        return cls(module=module, qualname=qualname)

    def resolve(self) -> Callable[..., Any]:
        """Import the module and return the callable. No allowlist is applied.

        Prefer :meth:`FunctionRegistry.resolve`, which applies the deployment's
        policy. This method exists for trusted, in-process use.
        """
        try:
            module = importlib.import_module(self.module)
        except ImportError as exc:
            raise FunctionResolutionError(f"cannot import module {self.module!r}: {exc}") from exc
        target: Any = module
        for part in self.qualname.split("."):
            try:
                target = getattr(target, part)
            except AttributeError as exc:
                raise FunctionResolutionError(
                    f"{self.module!r} has no attribute {self.qualname!r}"
                ) from exc
        if not callable(target):
            raise FunctionResolutionError(f"{self.path!r} resolved to a non-callable")
        return cast(Callable[..., Any], target)


class FunctionRegistry:
    """Maps task names to callables, with an import policy.

    Resolution order:

    1. an explicit registration under that exact name;
    2. dynamic import, if :attr:`allow_import` and the module passes the
       allowlist.

    Instances are not thread-safe for concurrent *registration*; register at
    import time (the normal pattern) and the read path is then safe to share
    across threads.
    """

    def __init__(
        self,
        *,
        allow_import: bool = True,
        allowed_modules: Sequence[str] = (),
    ) -> None:
        self._functions: dict[str, Callable[..., Any]] = {}
        self.allow_import = allow_import
        self.allowed_modules: tuple[str, ...] = tuple(allowed_modules)

    def register(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        replace: bool = False,
    ) -> Callable[..., Any]:
        """Register ``func``, returning it so this works as a decorator::

        @registry.register
        def send_email(to: str) -> None: ...
        """
        key = name or FunctionRef.from_callable(func).path
        if key in self._functions and not replace and self._functions[key] is not func:
            raise FunctionResolutionError(f"{key!r} is already registered to a different callable")
        self._functions[key] = func
        return func

    def unregister(self, name: str) -> None:
        self._functions.pop(name, None)

    def clear(self) -> None:
        self._functions.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._functions

    def __iter__(self) -> Iterator[str]:
        return iter(self._functions)

    def __len__(self) -> int:
        return len(self._functions)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._functions))

    def resolve(self, name: str) -> Callable[..., Any]:
        """Return the callable for ``name``, applying the import policy."""
        registered = self._functions.get(name)
        if registered is not None:
            return registered
        if not self.allow_import:
            known = ", ".join(self.names()) or "<none>"
            raise FunctionResolutionError(
                f"{name!r} is not registered and dynamic import is disabled (registered: {known})"
            )
        ref = FunctionRef.parse(name)
        if not self._module_allowed(ref.module):
            allowed = ", ".join(self.allowed_modules)
            raise FunctionResolutionError(
                f"module {ref.module!r} is not in the import allowlist ({allowed})"
            )
        return ref.resolve()

    def reference(self, target: Callable[..., Any] | str | FunctionRef) -> FunctionRef:
        """Normalise anything nameable into a :class:`FunctionRef`.

        Registering the callable at the same time is deliberate: the process that
        submits a task usually also runs it in tests and in inline mode, and an
        unregistered-but-submittable name is a foot-gun.
        """
        if isinstance(target, FunctionRef):
            return target
        if isinstance(target, str):
            return FunctionRef.parse(target)
        ref = FunctionRef.from_callable(target)
        self._functions.setdefault(ref.path, target)
        return ref

    def _module_allowed(self, module: str) -> bool:
        if not self.allowed_modules:
            return True
        return any(
            module == allowed or module.startswith(f"{allowed}.")
            for allowed in self.allowed_modules
        )


def is_async_callable(func: Callable[..., Any]) -> bool:
    """Whether calling ``func`` returns a coroutine.

    Unwraps ``functools.partial`` and looks through ``__call__`` so async
    callable objects are detected, not just plain ``async def``.
    """
    unwrapped = func
    while hasattr(unwrapped, "func"):  # functools.partial and friends
        unwrapped = unwrapped.func
    if inspect.iscoroutinefunction(unwrapped):
        return True
    call = getattr(unwrapped, "__call__", None)  # noqa: B004 - intentional
    return call is not None and inspect.iscoroutinefunction(call)


__all__ = ["FunctionRef", "FunctionRegistry", "is_async_callable"]
