import typing as t
from ast import literal_eval
from ast import parse
from itertools import chain
from itertools import islice
from types import GeneratorType

from . import nodes
from .compiler import CodeGenerator
from .compiler import Frame
from .compiler import has_safe_repr
from .environment import Environment
from .environment import Template


def native_concat(values: t.Iterable[t.Any]) -> t.Any | None:
    """Return a native Python type from the list of compiled nodes. If
    the result is a single node, its value is returned. Otherwise, the
    nodes are concatenated as strings. If the result can be parsed with
    :func:`ast.literal_eval`, the parsed value is returned. Otherwise,
    the string is returned.

    :param values: Iterable of outputs to concatenate.
    """
    pass


class NativeCodeGenerator(CodeGenerator):
    """A code generator which renders Python types by not adding
    ``str()`` around output nodes.
    """

    @staticmethod
    def _default_finalize(value: t.Any) -> t.Any:
        pass

    def _output_const_repr(self, group: t.Iterable[t.Any]) -> str:
        pass

    def _output_child_to_const(
        self, node: nodes.Expr, frame: Frame, finalize: CodeGenerator._FinalizeInfo
    ) -> t.Any:
        pass

    def _output_child_pre(
        self, node: nodes.Expr, frame: Frame, finalize: CodeGenerator._FinalizeInfo
    ) -> None:
        pass

    def _output_child_post(
        self, node: nodes.Expr, frame: Frame, finalize: CodeGenerator._FinalizeInfo
    ) -> None:
        pass


class NativeEnvironment(Environment):
    """An environment that renders templates to native Python types."""

    code_generator_class = NativeCodeGenerator
    concat = staticmethod(native_concat)  # type: ignore


class NativeTemplate(Template):
    environment_class = NativeEnvironment

    def render(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        """Render the template to produce a native Python type. If the
        result is a single node, its value is returned. Otherwise, the
        nodes are concatenated as strings. If the result can be parsed
        with :func:`ast.literal_eval`, the parsed value is returned.
        Otherwise, the string is returned.
        """
        ctx = self.new_context(dict(*args, **kwargs))

        try:
            return self.environment_class.concat(  # type: ignore
                self.root_render_func(ctx)
            )
        except Exception:
            return self.environment.handle_exception()

    async def render_async(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if not self.environment.is_async:
            raise RuntimeError(
                "The environment was not created with async mode enabled."
            )

        ctx = self.new_context(dict(*args, **kwargs))

        try:
            return self.environment_class.concat(  # type: ignore
                [n async for n in self.root_render_func(ctx)]  # type: ignore
            )
        except Exception:
            return self.environment.handle_exception()


NativeEnvironment.template_class = NativeTemplate
