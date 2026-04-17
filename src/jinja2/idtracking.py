import typing as t

from . import nodes
from .visitor import NodeVisitor

if t.TYPE_CHECKING:
    import typing_extensions as te

VAR_LOAD_PARAMETER = "param"
VAR_LOAD_RESOLVE = "resolve"
VAR_LOAD_ALIAS = "alias"
VAR_LOAD_UNDEFINED = "undefined"


def find_symbols(
    nodes: t.Iterable[nodes.Node], parent_symbols: t.Optional["Symbols"] = None
) -> "Symbols":
    pass


def symbols_for_node(
    node: nodes.Node, parent_symbols: t.Optional["Symbols"] = None
) -> "Symbols":
    sym = Symbols(parent=parent_symbols)
    sym.analyze_node(node)
    return sym


class Symbols:
    def __init__(
        self, parent: t.Optional["Symbols"] = None, level: int | None = None
    ) -> None:
        if level is None:
            if parent is None:
                level = 0
            else:
                level = parent.level + 1

        self.level: int = level
        self.parent = parent
        self.refs: dict[str, str] = {}
        self.loads: dict[str, t.Any] = {}
        self.stores: set[str] = set()

    def analyze_node(self, node: nodes.Node, **kwargs: t.Any) -> None:
        visitor = RootVisitor(self)
        visitor.visit(node, **kwargs)

    def _define_ref(self, name: str, load: tuple[str, str | None] | None = None) -> str:
        pass

    def find_load(self, target: str) -> t.Any | None:
        pass

    def find_ref(self, name: str) -> str | None:
        pass

    def ref(self, name: str) -> str:
        pass

    def copy(self) -> "te.Self":
        rv = object.__new__(self.__class__)
        rv.__dict__.update(self.__dict__)
        rv.refs = self.refs.copy()
        rv.loads = self.loads.copy()
        rv.stores = self.stores.copy()
        return rv

    def store(self, name: str) -> None:
        pass

    def declare_parameter(self, name: str) -> str:
        pass

    def load(self, name: str) -> None:
        pass

    def branch_update(self, branch_symbols: t.Sequence["Symbols"]) -> None:
        pass

    def dump_stores(self) -> dict[str, str]:
        pass

    def dump_param_targets(self) -> set[str]:
        pass


class RootVisitor(NodeVisitor):
    def __init__(self, symbols: "Symbols") -> None:
        self.sym_visitor = FrameSymbolVisitor(symbols)

    def _simple_visit(self, node: nodes.Node, **kwargs: t.Any) -> None:
        pass

    visit_Template = _simple_visit
    visit_Block = _simple_visit
    visit_Macro = _simple_visit
    visit_FilterBlock = _simple_visit
    visit_Scope = _simple_visit
    visit_If = _simple_visit
    visit_ScopedEvalContextModifier = _simple_visit

    def visit_AssignBlock(self, node: nodes.AssignBlock, **kwargs: t.Any) -> None:
        pass

    def visit_CallBlock(self, node: nodes.CallBlock, **kwargs: t.Any) -> None:
        pass

    def visit_OverlayScope(self, node: nodes.OverlayScope, **kwargs: t.Any) -> None:
        pass

    def visit_For(
        self, node: nodes.For, for_branch: str = "body", **kwargs: t.Any
    ) -> None:
        pass

    def visit_With(self, node: nodes.With, **kwargs: t.Any) -> None:
        pass

    def generic_visit(self, node: nodes.Node, *args: t.Any, **kwargs: t.Any) -> None:
        raise NotImplementedError(f"Cannot find symbols for {type(node).__name__!r}")


class FrameSymbolVisitor(NodeVisitor):
    """A visitor for `Frame.inspect`."""

    def __init__(self, symbols: "Symbols") -> None:
        self.symbols = symbols

    def visit_Name(
        self, node: nodes.Name, store_as_param: bool = False, **kwargs: t.Any
    ) -> None:
        """All assignments to names go through this function."""
        pass

    def visit_NSRef(self, node: nodes.NSRef, **kwargs: t.Any) -> None:
        pass

    def visit_If(self, node: nodes.If, **kwargs: t.Any) -> None:
        pass

    def visit_Macro(self, node: nodes.Macro, **kwargs: t.Any) -> None:
        pass

    def visit_Import(self, node: nodes.Import, **kwargs: t.Any) -> None:
        pass

    def visit_FromImport(self, node: nodes.FromImport, **kwargs: t.Any) -> None:
        pass

    def visit_Assign(self, node: nodes.Assign, **kwargs: t.Any) -> None:
        """Visit assignments in the correct order."""
        pass

    def visit_For(self, node: nodes.For, **kwargs: t.Any) -> None:
        """Visiting stops at for blocks.  However the block sequence
        is visited as part of the outer scope.
        """
        pass

    def visit_CallBlock(self, node: nodes.CallBlock, **kwargs: t.Any) -> None:
        pass

    def visit_FilterBlock(self, node: nodes.FilterBlock, **kwargs: t.Any) -> None:
        pass

    def visit_With(self, node: nodes.With, **kwargs: t.Any) -> None:
        pass

    def visit_AssignBlock(self, node: nodes.AssignBlock, **kwargs: t.Any) -> None:
        """Stop visiting at block assigns."""
        pass

    def visit_Scope(self, node: nodes.Scope, **kwargs: t.Any) -> None:
        """Stop visiting at scopes."""

    def visit_Block(self, node: nodes.Block, **kwargs: t.Any) -> None:
        """Stop visiting at blocks."""

    def visit_OverlayScope(self, node: nodes.OverlayScope, **kwargs: t.Any) -> None:
        """Do not visit into overlay scopes."""
