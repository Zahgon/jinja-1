"""Compiles nodes from the parser into Python code."""

import typing as t
from contextlib import contextmanager
from functools import update_wrapper
from io import StringIO
from itertools import chain
from keyword import iskeyword as is_python_keyword

from markupsafe import escape
from markupsafe import Markup

from . import nodes
from .exceptions import TemplateAssertionError
from .idtracking import Symbols
from .idtracking import VAR_LOAD_ALIAS
from .idtracking import VAR_LOAD_PARAMETER
from .idtracking import VAR_LOAD_RESOLVE
from .idtracking import VAR_LOAD_UNDEFINED
from .nodes import EvalContext
from .optimizer import Optimizer
from .utils import _PassArg
from .utils import concat
from .visitor import NodeVisitor

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .environment import Environment

F = t.TypeVar("F", bound=t.Callable[..., t.Any])

operators = {
    "eq": "==",
    "ne": "!=",
    "gt": ">",
    "gteq": ">=",
    "lt": "<",
    "lteq": "<=",
    "in": "in",
    "notin": "not in",
}


def optimizeconst(f: F) -> F:
    pass


def _make_binop(op: str) -> t.Callable[["CodeGenerator", nodes.BinExpr, "Frame"], None]:
    @optimizeconst
    def visitor(self: "CodeGenerator", node: nodes.BinExpr, frame: Frame) -> None:
        pass

    return visitor


def _make_unop(
    op: str,
) -> t.Callable[["CodeGenerator", nodes.UnaryExpr, "Frame"], None]:
    @optimizeconst
    def visitor(self: "CodeGenerator", node: nodes.UnaryExpr, frame: Frame) -> None:
        pass

    return visitor


def generate(
    node: nodes.Template,
    environment: "Environment",
    name: str | None,
    filename: str | None,
    stream: t.TextIO | None = None,
    defer_init: bool = False,
    optimized: bool = True,
) -> str | None:
    """Generate the python source for a node tree."""
    if not isinstance(node, nodes.Template):
        raise TypeError("Can't compile non template nodes")

    generator = environment.code_generator_class(
        environment, name, filename, stream, defer_init, optimized
    )
    generator.visit(node)

    if stream is None:
        return generator.stream.getvalue()  # type: ignore

    return None


def has_safe_repr(value: t.Any) -> bool:
    """Does the node have a safe representation?"""
    if value is None or value is NotImplemented or value is Ellipsis:
        return True

    if type(value) in {bool, int, float, complex, range, str, Markup}:
        return True

    if type(value) in {tuple, list, set, frozenset}:
        return all(has_safe_repr(v) for v in value)

    if type(value) is dict:  # noqa E721
        return all(has_safe_repr(k) and has_safe_repr(v) for k, v in value.items())

    return False


def find_undeclared(nodes: t.Iterable[nodes.Node], names: t.Iterable[str]) -> set[str]:
    """Check if the names passed are accessed undeclared.  The return value
    is a set of all the undeclared names from the sequence of names found.
    """
    pass


class MacroRef:
    def __init__(self, node: nodes.Macro | nodes.CallBlock) -> None:
        self.node = node
        self.accesses_caller = False
        self.accesses_kwargs = False
        self.accesses_varargs = False


class Frame:
    """Holds compile time information for us."""

    def __init__(
        self,
        eval_ctx: EvalContext,
        parent: t.Optional["Frame"] = None,
        level: int | None = None,
    ) -> None:
        self.eval_ctx = eval_ctx

        # the parent of this frame
        self.parent = parent

        if parent is None:
            self.symbols = Symbols(level=level)

            # in some dynamic inheritance situations the compiler needs to add
            # write tests around output statements.
            self.require_output_check = False

            # inside some tags we are using a buffer rather than yield statements.
            # this for example affects {% filter %} or {% macro %}.  If a frame
            # is buffered this variable points to the name of the list used as
            # buffer.
            self.buffer: str | None = None

            # the name of the block we're in, otherwise None.
            self.block: str | None = None

        else:
            self.symbols = Symbols(parent.symbols, level=level)
            self.require_output_check = parent.require_output_check
            self.buffer = parent.buffer
            self.block = parent.block

        # a toplevel frame is the root + soft frames such as if conditions.
        self.toplevel = False

        # the root frame is basically just the outermost frame, so no if
        # conditions.  This information is used to optimize inheritance
        # situations.
        self.rootlevel = False

        # variables set inside of loops and blocks should not affect outer frames,
        # but they still needs to be kept track of as part of the active context.
        self.loop_frame = False
        self.block_frame = False

        # track whether the frame is being used in an if-statement or conditional
        # expression as it determines which errors should be raised during runtime
        # or compile time.
        self.soft_frame = False

    def copy(self) -> "te.Self":
        """Create a copy of the current one."""
        rv = object.__new__(self.__class__)
        rv.__dict__.update(self.__dict__)
        rv.symbols = self.symbols.copy()
        return rv

    def inner(self, isolated: bool = False) -> "Frame":
        """Return an inner frame."""
        pass

    def soft(self) -> "te.Self":
        """Return a soft frame.  A soft frame may not be modified as
        standalone thing as it shares the resources with the frame it
        was created of, but it's not a rootlevel frame any longer.

        This is only used to implement if-statements and conditional
        expressions.
        """
        pass

    __copy__ = copy


class VisitorExit(RuntimeError):
    """Exception used by the `UndeclaredNameVisitor` to signal a stop."""


class DependencyFinderVisitor(NodeVisitor):
    """A visitor that collects filter and test calls."""

    def __init__(self) -> None:
        self.filters: set[str] = set()
        self.tests: set[str] = set()

    def visit_Filter(self, node: nodes.Filter) -> None:
        pass

    def visit_Test(self, node: nodes.Test) -> None:
        pass

    def visit_Block(self, node: nodes.Block) -> None:
        """Stop visiting at blocks."""


class UndeclaredNameVisitor(NodeVisitor):
    """A visitor that checks if a name is accessed without being
    declared.  This is different from the frame visitor as it will
    not stop at closure frames.
    """

    def __init__(self, names: t.Iterable[str]) -> None:
        self.names = set(names)
        self.undeclared: set[str] = set()

    def visit_Name(self, node: nodes.Name) -> None:
        pass

    def visit_Block(self, node: nodes.Block) -> None:
        """Stop visiting a blocks."""


class CompilerExit(Exception):
    """Raised if the compiler encountered a situation where it just
    doesn't make sense to further process the code.  Any block that
    raises such an exception is not further processed.
    """


class CodeGenerator(NodeVisitor):
    def __init__(
        self,
        environment: "Environment",
        name: str | None,
        filename: str | None,
        stream: t.TextIO | None = None,
        defer_init: bool = False,
        optimized: bool = True,
    ) -> None:
        if stream is None:
            stream = StringIO()
        self.environment = environment
        self.name = name
        self.filename = filename
        self.stream = stream
        self.created_block_context = False
        self.defer_init = defer_init
        self.optimizer: Optimizer | None = None

        if optimized:
            self.optimizer = Optimizer(environment)

        # aliases for imports
        self.import_aliases: dict[str, str] = {}

        # a registry for all blocks.  Because blocks are moved out
        # into the global python scope they are registered here
        self.blocks: dict[str, nodes.Block] = {}

        # the number of extends statements so far
        self.extends_so_far = 0

        # some templates have a rootlevel extends.  In this case we
        # can safely assume that we're a child template and do some
        # more optimizations.
        self.has_known_extends = False

        # the current line number
        self.code_lineno = 1

        # registry of all filters and tests (global, not block local)
        self.tests: dict[str, str] = {}
        self.filters: dict[str, str] = {}

        # the debug information
        self.debug_info: list[tuple[int, int]] = []
        self._write_debug_info: int | None = None

        # the number of new lines before the next write()
        self._new_lines = 0

        # the line number of the last written statement
        self._last_line = 0

        # true if nothing was written so far.
        self._first_write = True

        # used by the `temporary_identifier` method to get new
        # unique, temporary identifier
        self._last_identifier = 0

        # the current indentation
        self._indentation = 0

        # Tracks toplevel assignments
        self._assign_stack: list[set[str]] = []

        # Tracks parameter definition blocks
        self._param_def_block: list[set[str]] = []

        # Tracks the current context.
        self._context_reference_stack = ["context"]

    @property
    def optimized(self) -> bool:
        pass

    # -- Various compilation helpers

    def fail(self, msg: str, lineno: int) -> "te.NoReturn":
        """Fail with a :exc:`TemplateAssertionError`."""
        raise TemplateAssertionError(msg, lineno, self.name, self.filename)

    def temporary_identifier(self) -> str:
        """Get a new unique identifier."""
        pass

    def buffer(self, frame: Frame) -> None:
        """Enable buffering for the frame from that point onwards."""
        pass

    def return_buffer_contents(
        self, frame: Frame, force_unescaped: bool = False
    ) -> None:
        """Return the buffer contents of the frame."""
        pass

    def indent(self) -> None:
        """Indent by one."""
        pass

    def outdent(self, step: int = 1) -> None:
        """Outdent by step."""
        pass

    def start_write(self, frame: Frame, node: nodes.Node | None = None) -> None:
        """Yield or write into the frame buffer."""
        pass

    def end_write(self, frame: Frame) -> None:
        """End the writing process started by `start_write`."""
        pass

    def simple_write(
        self, s: str, frame: Frame, node: nodes.Node | None = None
    ) -> None:
        """Simple shortcut for start_write + write + end_write."""
        pass

    def blockvisit(self, nodes: t.Iterable[nodes.Node], frame: Frame) -> None:
        """Visit a list of nodes as block in a frame.  If the current frame
        is no buffer a dummy ``if 0: yield None`` is written automatically.
        """
        pass

    def write(self, x: str) -> None:
        """Write a string into the output stream."""
        if self._new_lines:
            if not self._first_write:
                self.stream.write("\n" * self._new_lines)
                self.code_lineno += self._new_lines
                if self._write_debug_info is not None:
                    self.debug_info.append((self._write_debug_info, self.code_lineno))
                    self._write_debug_info = None
            self._first_write = False
            self.stream.write("    " * self._indentation)
            self._new_lines = 0
        self.stream.write(x)

    def writeline(self, x: str, node: nodes.Node | None = None, extra: int = 0) -> None:
        """Combination of newline and write."""
        pass

    def newline(self, node: nodes.Node | None = None, extra: int = 0) -> None:
        """Add one or more newlines before the next write."""
        pass

    def signature(
        self,
        node: nodes.Call | nodes.Filter | nodes.Test,
        frame: Frame,
        extra_kwargs: t.Mapping[str, t.Any] | None = None,
    ) -> None:
        """Writes a function call to the stream for the current node.
        A leading comma is added automatically.  The extra keyword
        arguments may not include python keywords otherwise a syntax
        error could occur.  The extra keyword arguments should be given
        as python dict.
        """
        # if any of the given keyword arguments is a python keyword
        # we have to make sure that no invalid call is created.
        kwarg_workaround = any(
            is_python_keyword(t.cast(str, k))
            for k in chain((x.key for x in node.kwargs), extra_kwargs or ())
        )

        for arg in node.args:
            self.write(", ")
            self.visit(arg, frame)

        if not kwarg_workaround:
            for kwarg in node.kwargs:
                self.write(", ")
                self.visit(kwarg, frame)
            if extra_kwargs is not None:
                for key, value in extra_kwargs.items():
                    self.write(f", {key}={value}")
        if node.dyn_args:
            self.write(", *")
            self.visit(node.dyn_args, frame)

        if kwarg_workaround:
            if node.dyn_kwargs is not None:
                self.write(", **dict({")
            else:
                self.write(", **{")
            for kwarg in node.kwargs:
                self.write(f"{kwarg.key!r}: ")
                self.visit(kwarg.value, frame)
                self.write(", ")
            if extra_kwargs is not None:
                for key, value in extra_kwargs.items():
                    self.write(f"{key!r}: {value}, ")
            if node.dyn_kwargs is not None:
                self.write("}, **")
                self.visit(node.dyn_kwargs, frame)
                self.write(")")
            else:
                self.write("}")

        elif node.dyn_kwargs is not None:
            self.write(", **")
            self.visit(node.dyn_kwargs, frame)

    def pull_dependencies(self, nodes: t.Iterable[nodes.Node]) -> None:
        """Find all filter and test names used in the template and
        assign them to variables in the compiled namespace. Checking
        that the names are registered with the environment is done when
        compiling the Filter and Test nodes. If the node is in an If or
        CondExpr node, the check is done at runtime instead.

        .. versionchanged:: 3.0
            Filters and tests in If and CondExpr nodes are checked at
            runtime instead of compile time.
        """
        pass

    def enter_frame(self, frame: Frame) -> None:
        pass

    def leave_frame(self, frame: Frame, with_python_scope: bool = False) -> None:
        pass

    def choose_async(self, async_value: str = "async ", sync_value: str = "") -> str:
        return async_value if self.environment.is_async else sync_value

    def func(self, name: str) -> str:
        return f"{self.choose_async()}def {name}"

    def macro_body(
        self, node: nodes.Macro | nodes.CallBlock, frame: Frame
    ) -> tuple[Frame, MacroRef]:
        """Dump the function def of a macro or call block."""
        pass

    def macro_def(self, macro_ref: MacroRef, frame: Frame) -> None:
        """Dump the macro definition for the def created by macro_body."""
        pass

    def position(self, node: nodes.Node) -> str:
        """Return a human readable position for the node."""
        pass

    def dump_local_context(self, frame: Frame) -> str:
        pass

    def write_commons(self) -> None:
        """Writes a common preamble that is used by root and block functions.
        Primarily this sets up common local helpers and enforces a generator
        through a dead branch.
        """
        pass

    def push_parameter_definitions(self, frame: Frame) -> None:
        """Pushes all parameter targets from the given frame into a local
        stack that permits tracking of yet to be assigned parameters.  In
        particular this enables the optimization from `visit_Name` to skip
        undefined expressions for parameters in macros as macros can reference
        otherwise unbound parameters.
        """
        pass

    def pop_parameter_definitions(self) -> None:
        """Pops the current parameter definitions set."""
        pass

    def mark_parameter_stored(self, target: str) -> None:
        """Marks a parameter in the current parameter definitions as stored.
        This will skip the enforced undefined checks.
        """
        pass

    def push_context_reference(self, target: str) -> None:
        pass

    def pop_context_reference(self) -> None:
        pass

    def get_context_ref(self) -> str:
        pass

    def get_resolve_func(self) -> str:
        pass

    def derive_context(self, frame: Frame) -> str:
        pass

    def parameter_is_undeclared(self, target: str) -> bool:
        """Checks if a given target is an undeclared parameter."""
        pass

    def push_assign_tracking(self) -> None:
        """Pushes a new layer for assignment tracking."""
        pass

    def pop_assign_tracking(self, frame: Frame) -> None:
        """Pops the topmost level for assignment tracking and updates the
        context variables if necessary.
        """
        pass

    # -- Statement Visitors

    def visit_Template(self, node: nodes.Template, frame: Frame | None = None) -> None:
        pass

    def visit_Block(self, node: nodes.Block, frame: Frame) -> None:
        """Call a block and register it for the template."""
        pass

    def visit_Extends(self, node: nodes.Extends, frame: Frame) -> None:
        """Calls the extender."""
        pass

    def visit_Include(self, node: nodes.Include, frame: Frame) -> None:
        """Handles includes."""
        pass

    def _import_common(
        self, node: nodes.Import | nodes.FromImport, frame: Frame
    ) -> None:
        pass

    def visit_Import(self, node: nodes.Import, frame: Frame) -> None:
        """Visit regular imports."""
        pass

    def visit_FromImport(self, node: nodes.FromImport, frame: Frame) -> None:
        """Visit named imports."""
        pass

    def visit_For(self, node: nodes.For, frame: Frame) -> None:
        pass

    def visit_If(self, node: nodes.If, frame: Frame) -> None:
        pass

    def visit_Macro(self, node: nodes.Macro, frame: Frame) -> None:
        pass

    def visit_CallBlock(self, node: nodes.CallBlock, frame: Frame) -> None:
        pass

    def visit_FilterBlock(self, node: nodes.FilterBlock, frame: Frame) -> None:
        pass

    def visit_With(self, node: nodes.With, frame: Frame) -> None:
        pass

    def visit_ExprStmt(self, node: nodes.ExprStmt, frame: Frame) -> None:
        pass

    class _FinalizeInfo(t.NamedTuple):
        const: t.Callable[..., str] | None
        src: str | None

    @staticmethod
    def _default_finalize(value: t.Any) -> t.Any:
        """The default finalize function if the environment isn't
        configured with one. Or, if the environment has one, this is
        called on that function's output for constants.
        """
        pass

    _finalize: _FinalizeInfo | None = None

    def _make_finalize(self) -> _FinalizeInfo:
        """Build the finalize function to be used on constants and at
        runtime. Cached so it's only created once for all output nodes.

        Returns a ``namedtuple`` with the following attributes:

        ``const``
            A function to finalize constant data at compile time.

        ``src``
            Source code to output around nodes to be evaluated at
            runtime.
        """
        pass

    def _output_const_repr(self, group: t.Iterable[t.Any]) -> str:
        """Given a group of constant values converted from ``Output``
        child nodes, produce a string to write to the template module
        source.
        """
        pass

    def _output_child_to_const(
        self, node: nodes.Expr, frame: Frame, finalize: _FinalizeInfo
    ) -> str:
        """Try to optimize a child of an ``Output`` node by trying to
        convert it to constant, finalized data at compile time.

        If :exc:`Impossible` is raised, the node is not constant and
        will be evaluated at runtime. Any other exception will also be
        evaluated at runtime for easier debugging.
        """
        pass

    def _output_child_pre(
        self, node: nodes.Expr, frame: Frame, finalize: _FinalizeInfo
    ) -> None:
        """Output extra source code before visiting a child of an
        ``Output`` node.
        """
        pass

    def _output_child_post(
        self, node: nodes.Expr, frame: Frame, finalize: _FinalizeInfo
    ) -> None:
        """Output extra source code after visiting a child of an
        ``Output`` node.
        """
        pass

    def visit_Output(self, node: nodes.Output, frame: Frame) -> None:
        # If an extends is active, don't render outside a block.
        pass

    def visit_Assign(self, node: nodes.Assign, frame: Frame) -> None:
        pass

    def visit_AssignBlock(self, node: nodes.AssignBlock, frame: Frame) -> None:
        pass

    # -- Expression Visitors

    def visit_Name(self, node: nodes.Name, frame: Frame) -> None:
        pass

    def visit_NSRef(self, node: nodes.NSRef, frame: Frame) -> None:
        # NSRef is a dotted assignment target a.b=c, but uses a[b]=c internally.
        # visit_Assign emits code to validate that each ref is to a Namespace
        # object only. That can't be emitted here as the ref could be in the
        # middle of a tuple assignment.
        pass

    def visit_Const(self, node: nodes.Const, frame: Frame) -> None:
        pass

    def visit_TemplateData(self, node: nodes.TemplateData, frame: Frame) -> None:
        pass

    def visit_Tuple(self, node: nodes.Tuple, frame: Frame) -> None:
        pass

    def visit_List(self, node: nodes.List, frame: Frame) -> None:
        pass

    def visit_Dict(self, node: nodes.Dict, frame: Frame) -> None:
        pass

    visit_Add = _make_binop("+")
    visit_Sub = _make_binop("-")
    visit_Mul = _make_binop("*")
    visit_Div = _make_binop("/")
    visit_FloorDiv = _make_binop("//")
    visit_Pow = _make_binop("**")
    visit_Mod = _make_binop("%")
    visit_And = _make_binop("and")
    visit_Or = _make_binop("or")
    visit_Pos = _make_unop("+")
    visit_Neg = _make_unop("-")
    visit_Not = _make_unop("not ")

    @optimizeconst
    def visit_Concat(self, node: nodes.Concat, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_Compare(self, node: nodes.Compare, frame: Frame) -> None:
        pass

    def visit_Operand(self, node: nodes.Operand, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_Getattr(self, node: nodes.Getattr, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_Getitem(self, node: nodes.Getitem, frame: Frame) -> None:
        # slices bypass the environment getitem method.
        pass

    def visit_Slice(self, node: nodes.Slice, frame: Frame) -> None:
        pass

    @contextmanager
    def _filter_test_common(
        self, node: nodes.Filter | nodes.Test, frame: Frame, is_filter: bool
    ) -> t.Iterator[None]:
        if self.environment.is_async:
            self.write("(await auto_await(")

        if is_filter:
            self.write(f"{self.filters[node.name]}(")
            func = self.environment.filters.get(node.name)
        else:
            self.write(f"{self.tests[node.name]}(")
            func = self.environment.tests.get(node.name)

        # When inside an If or CondExpr frame, allow the filter to be
        # undefined at compile time and only raise an error if it's
        # actually called at runtime. See pull_dependencies.
        if func is None and not frame.soft_frame:
            type_name = "filter" if is_filter else "test"
            self.fail(f"No {type_name} named {node.name!r}.", node.lineno)

        pass_arg = {
            _PassArg.context: "context",
            _PassArg.eval_context: "context.eval_ctx",
            _PassArg.environment: "environment",
        }.get(
            _PassArg.from_obj(func)  # type: ignore
        )

        if pass_arg is not None:
            self.write(f"{pass_arg}, ")

        # Back to the visitor function to handle visiting the target of
        # the filter or test.
        yield

        self.signature(node, frame)
        self.write(")")

        if self.environment.is_async:
            self.write("))")

    @optimizeconst
    def visit_Filter(self, node: nodes.Filter, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_Test(self, node: nodes.Test, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_CondExpr(self, node: nodes.CondExpr, frame: Frame) -> None:
        pass

    @optimizeconst
    def visit_Call(
        self, node: nodes.Call, frame: Frame, forward_caller: bool = False
    ) -> None:
        pass

    def visit_Keyword(self, node: nodes.Keyword, frame: Frame) -> None:
        pass

    # -- Unused nodes for extensions

    def visit_MarkSafe(self, node: nodes.MarkSafe, frame: Frame) -> None:
        pass

    def visit_MarkSafeIfAutoescape(
        self, node: nodes.MarkSafeIfAutoescape, frame: Frame
    ) -> None:
        pass

    def visit_EnvironmentAttribute(
        self, node: nodes.EnvironmentAttribute, frame: Frame
    ) -> None:
        pass

    def visit_ExtensionAttribute(
        self, node: nodes.ExtensionAttribute, frame: Frame
    ) -> None:
        pass

    def visit_ImportedName(self, node: nodes.ImportedName, frame: Frame) -> None:
        pass

    def visit_InternalName(self, node: nodes.InternalName, frame: Frame) -> None:
        pass

    def visit_ContextReference(
        self, node: nodes.ContextReference, frame: Frame
    ) -> None:
        pass

    def visit_DerivedContextReference(
        self, node: nodes.DerivedContextReference, frame: Frame
    ) -> None:
        pass

    def visit_Continue(self, node: nodes.Continue, frame: Frame) -> None:
        pass

    def visit_Break(self, node: nodes.Break, frame: Frame) -> None:
        pass

    def visit_Scope(self, node: nodes.Scope, frame: Frame) -> None:
        pass

    def visit_OverlayScope(self, node: nodes.OverlayScope, frame: Frame) -> None:
        pass

    def visit_EvalContextModifier(
        self, node: nodes.EvalContextModifier, frame: Frame
    ) -> None:
        pass

    def visit_ScopedEvalContextModifier(
        self, node: nodes.ScopedEvalContextModifier, frame: Frame
    ) -> None:
        pass
