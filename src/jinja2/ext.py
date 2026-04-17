"""Extension API for adding custom tags and behavior."""

import pprint
import re
import typing as t

from markupsafe import Markup

from . import defaults
from . import nodes
from .environment import Environment
from .exceptions import TemplateAssertionError
from .exceptions import TemplateSyntaxError
from .runtime import concat  # type: ignore
from .runtime import Context
from .runtime import Undefined
from .utils import import_string
from .utils import pass_context

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .lexer import Token
    from .lexer import TokenStream
    from .parser import Parser

    class _TranslationsBasic(te.Protocol):
        def gettext(self, message: str) -> str: ...

        def ngettext(self, singular: str, plural: str, n: int) -> str:
            pass

    class _TranslationsContext(_TranslationsBasic):
        def pgettext(self, context: str, message: str) -> str: ...

        def npgettext(
            self, context: str, singular: str, plural: str, n: int
        ) -> str: ...

    _SupportedTranslations = _TranslationsBasic | _TranslationsContext


# I18N functions available in Jinja templates. If the I18N library
# provides ugettext, it will be assigned to gettext.
GETTEXT_FUNCTIONS: tuple[str, ...] = (
    "_",
    "gettext",
    "ngettext",
    "pgettext",
    "npgettext",
)
_ws_re = re.compile(r"\s*\n\s*")


class Extension:
    """Extensions can be used to add extra functionality to the Jinja template
    system at the parser level.  Custom extensions are bound to an environment
    but may not store environment specific data on `self`.  The reason for
    this is that an extension can be bound to another environment (for
    overlays) by creating a copy and reassigning the `environment` attribute.

    As extensions are created by the environment they cannot accept any
    arguments for configuration.  One may want to work around that by using
    a factory function, but that is not possible as extensions are identified
    by their import name.  The correct way to configure the extension is
    storing the configuration values on the environment.  Because this way the
    environment ends up acting as central configuration storage the
    attributes may clash which is why extensions have to ensure that the names
    they choose for configuration are not too generic.  ``prefix`` for example
    is a terrible name, ``fragment_cache_prefix`` on the other hand is a good
    name as includes the name of the extension (fragment cache).
    """

    identifier: t.ClassVar[str]

    def __init_subclass__(cls) -> None:
        cls.identifier = f"{cls.__module__}.{cls.__name__}"

    #: if this extension parses this is the list of tags it's listening to.
    tags: set[str] = set()

    #: the priority of that extension.  This is especially useful for
    #: extensions that preprocess values.  A lower value means higher
    #: priority.
    #:
    #: .. versionadded:: 2.4
    priority = 100

    def __init__(self, environment: Environment) -> None:
        self.environment = environment

    def bind(self, environment: Environment) -> "te.Self":
        """Create a copy of this extension bound to another environment."""
        pass

    def preprocess(
        self, source: str, name: str | None, filename: str | None = None
    ) -> str:
        """This method is called before the actual lexing and can be used to
        preprocess the source.  The `filename` is optional.  The return value
        must be the preprocessed source.
        """
        pass

    def filter_stream(
        self, stream: "TokenStream"
    ) -> t.Union["TokenStream", t.Iterable["Token"]]:
        """It's passed a :class:`~jinja2.lexer.TokenStream` that can be used
        to filter tokens returned.  This method has to return an iterable of
        :class:`~jinja2.lexer.Token`\\s, but it doesn't have to return a
        :class:`~jinja2.lexer.TokenStream`.
        """
        pass

    def parse(self, parser: "Parser") -> nodes.Node | list[nodes.Node]:
        """If any of the :attr:`tags` matched this method is called with the
        parser as first argument.  The token the parser stream is pointing at
        is the name token that matched.  This method has to return one or a
        list of multiple nodes.
        """
        raise NotImplementedError()

    def attr(self, name: str, lineno: int | None = None) -> nodes.ExtensionAttribute:
        """Return an attribute node for the current extension.  This is useful
        to pass constants on extensions to generated template code.

        ::

            self.attr('_my_attribute', lineno=lineno)
        """
        return nodes.ExtensionAttribute(self.identifier, name, lineno=lineno)

    def call_method(
        self,
        name: str,
        args: list[nodes.Expr] | None = None,
        kwargs: list[nodes.Keyword] | None = None,
        dyn_args: nodes.Expr | None = None,
        dyn_kwargs: nodes.Expr | None = None,
        lineno: int | None = None,
    ) -> nodes.Call:
        """Call a method of the extension.  This is a shortcut for
        :meth:`attr` + :class:`jinja2.nodes.Call`.
        """
        if args is None:
            args = []
        if kwargs is None:
            kwargs = []
        return nodes.Call(
            self.attr(name, lineno=lineno),
            args,
            kwargs,
            dyn_args,
            dyn_kwargs,
            lineno=lineno,
        )


@pass_context
def _gettext_alias(
    __context: Context, *args: t.Any, **kwargs: t.Any
) -> t.Any | Undefined:
    pass


def _make_new_gettext(func: t.Callable[[str], str]) -> t.Callable[..., str]:
    @pass_context
    pass


def _make_new_ngettext(func: t.Callable[[str, str, int], str]) -> t.Callable[..., str]:
    @pass_context
    pass


def _make_new_pgettext(func: t.Callable[[str, str], str]) -> t.Callable[..., str]:
    @pass_context
    pass


def _make_new_npgettext(
    func: t.Callable[[str, str, str, int], str],
) -> t.Callable[..., str]:
    @pass_context
    pass


class InternationalizationExtension(Extension):
    """This extension adds gettext support to Jinja."""

    tags = {"trans"}

    # TODO: the i18n extension is currently reevaluating values in a few
    # situations.  Take this example:
    #   {% trans count=something() %}{{ count }} foo{% pluralize
    #     %}{{ count }} fooss{% endtrans %}
    # something is called twice here.  One time for the gettext value and
    # the other time for the n-parameter of the ngettext function.

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        environment.globals["_"] = _gettext_alias
        environment.extend(
            install_gettext_translations=self._install,
            install_null_translations=self._install_null,
            install_gettext_callables=self._install_callables,
            uninstall_gettext_translations=self._uninstall,
            extract_translations=self._extract,
            newstyle_gettext=False,
        )

    def _install(
        self, translations: "_SupportedTranslations", newstyle: bool | None = None
    ) -> None:
        # ugettext and ungettext are preferred in case the I18N library
        # is providing compatibility with older Python versions.
        pass

    def _install_null(self, newstyle: bool | None = None) -> None:
        pass

    def _install_callables(
        self,
        gettext: t.Callable[[str], str],
        ngettext: t.Callable[[str, str, int], str],
        newstyle: bool | None = None,
        pgettext: t.Callable[[str, str], str] | None = None,
        npgettext: t.Callable[[str, str, str, int], str] | None = None,
    ) -> None:
        pass

    def _uninstall(self, translations: "_SupportedTranslations") -> None:
        pass

    def _extract(
        self,
        source: str | nodes.Template,
        gettext_functions: t.Sequence[str] = GETTEXT_FUNCTIONS,
    ) -> t.Iterator[tuple[int, str, str | None | tuple[str | None, ...]]]:
        pass

    def parse(self, parser: "Parser") -> nodes.Node | list[nodes.Node]:
        """Parse a translatable tag."""
        lineno = next(parser.stream).lineno

        context = None
        context_token = parser.stream.next_if("string")

        if context_token is not None:
            context = context_token.value

        # find all the variables referenced.  Additionally a variable can be
        # defined in the body of the trans block too, but this is checked at
        # a later state.
        plural_expr: nodes.Expr | None = None
        plural_expr_assignment: nodes.Assign | None = None
        num_called_num = False
        variables: dict[str, nodes.Expr] = {}
        trimmed = None
        while parser.stream.current.type != "block_end":
            if variables:
                parser.stream.expect("comma")

            # skip colon for python compatibility
            if parser.stream.skip_if("colon"):
                break

            token = parser.stream.expect("name")
            if token.value in variables:
                parser.fail(
                    f"translatable variable {token.value!r} defined twice.",
                    token.lineno,
                    exc=TemplateAssertionError,
                )

            # expressions
            if parser.stream.current.type == "assign":
                next(parser.stream)
                variables[token.value] = var = parser.parse_expression()
            elif trimmed is None and token.value in ("trimmed", "notrimmed"):
                trimmed = token.value == "trimmed"
                continue
            else:
                variables[token.value] = var = nodes.Name(token.value, "load")

            if plural_expr is None:
                if isinstance(var, nodes.Call):
                    plural_expr = nodes.Name("_trans", "load")
                    variables[token.value] = plural_expr
                    plural_expr_assignment = nodes.Assign(
                        nodes.Name("_trans", "store"), var
                    )
                else:
                    plural_expr = var
                num_called_num = token.value == "num"

        parser.stream.expect("block_end")

        plural = None
        have_plural = False
        referenced = set()

        # now parse until endtrans or pluralize
        singular_names, singular = self._parse_block(parser, True)
        if singular_names:
            referenced.update(singular_names)
            if plural_expr is None:
                plural_expr = nodes.Name(singular_names[0], "load")
                num_called_num = singular_names[0] == "num"

        # if we have a pluralize block, we parse that too
        if parser.stream.current.test("name:pluralize"):
            have_plural = True
            next(parser.stream)
            if parser.stream.current.type != "block_end":
                token = parser.stream.expect("name")
                if token.value not in variables:
                    parser.fail(
                        f"unknown variable {token.value!r} for pluralization",
                        token.lineno,
                        exc=TemplateAssertionError,
                    )
                plural_expr = variables[token.value]
                num_called_num = token.value == "num"
            parser.stream.expect("block_end")
            plural_names, plural = self._parse_block(parser, False)
            next(parser.stream)
            referenced.update(plural_names)
        else:
            next(parser.stream)

        # register free names as simple name expressions
        for name in referenced:
            if name not in variables:
                variables[name] = nodes.Name(name, "load")

        if not have_plural:
            plural_expr = None
        elif plural_expr is None:
            parser.fail("pluralize without variables", lineno)

        if trimmed is None:
            trimmed = self.environment.policies["ext.i18n.trimmed"]
        if trimmed:
            singular = self._trim_whitespace(singular)
            if plural:
                plural = self._trim_whitespace(plural)

        node = self._make_node(
            singular,
            plural,
            context,
            variables,
            plural_expr,
            bool(referenced),
            num_called_num and have_plural,
        )
        node.set_lineno(lineno)
        if plural_expr_assignment is not None:
            return [plural_expr_assignment, node]
        else:
            return node

    def _trim_whitespace(self, string: str, _ws_re: t.Pattern[str] = _ws_re) -> str:
        return _ws_re.sub(" ", string.strip())

    def _parse_block(
        self, parser: "Parser", allow_pluralize: bool
    ) -> tuple[list[str], str]:
        """Parse until the next block tag with a given name."""
        referenced = []
        buf = []

        while True:
            if parser.stream.current.type == "data":
                buf.append(parser.stream.current.value.replace("%", "%%"))
                next(parser.stream)
            elif parser.stream.current.type == "variable_begin":
                next(parser.stream)
                name = parser.stream.expect("name").value
                referenced.append(name)
                buf.append(f"%({name})s")
                parser.stream.expect("variable_end")
            elif parser.stream.current.type == "block_begin":
                next(parser.stream)
                block_name = (
                    parser.stream.current.value
                    if parser.stream.current.type == "name"
                    else None
                )
                if block_name == "endtrans":
                    break
                elif block_name == "pluralize":
                    if allow_pluralize:
                        break
                    parser.fail(
                        "a translatable section can have only one pluralize section"
                    )
                elif block_name == "trans":
                    parser.fail(
                        "trans blocks can't be nested; did you mean `endtrans`?"
                    )
                parser.fail(
                    f"control structures in translatable sections are not allowed; "
                    f"saw `{block_name}`"
                )
            elif parser.stream.eos:
                parser.fail("unclosed translation block")
            else:
                raise RuntimeError("internal parser error")

        return referenced, concat(buf)

    def _make_node(
        self,
        singular: str,
        plural: str | None,
        context: str | None,
        variables: dict[str, nodes.Expr],
        plural_expr: nodes.Expr | None,
        vars_referenced: bool,
        num_called_num: bool,
    ) -> nodes.Output:
        """Generates a useful node from the data provided."""
        newstyle = self.environment.newstyle_gettext  # type: ignore
        node: nodes.Expr

        # no variables referenced?  no need to escape for old style
        # gettext invocations only if there are vars.
        if not vars_referenced and not newstyle:
            singular = singular.replace("%%", "%")
            if plural:
                plural = plural.replace("%%", "%")

        func_name = "gettext"
        func_args: list[nodes.Expr] = [nodes.Const(singular)]

        if context is not None:
            func_args.insert(0, nodes.Const(context))
            func_name = f"p{func_name}"

        if plural_expr is not None:
            func_name = f"n{func_name}"
            func_args.extend((nodes.Const(plural), plural_expr))

        node = nodes.Call(nodes.Name(func_name, "load"), func_args, [], None, None)

        # in case newstyle gettext is used, the method is powerful
        # enough to handle the variable expansion and autoescape
        # handling itself
        if newstyle:
            for key, value in variables.items():
                # the function adds that later anyways in case num was
                # called num, so just skip it.
                if num_called_num and key == "num":
                    continue
                node.kwargs.append(nodes.Keyword(key, value))

        # otherwise do that here
        else:
            # mark the return value as safe if we are in an
            # environment with autoescaping turned on
            node = nodes.MarkSafeIfAutoescape(node)
            if variables:
                node = nodes.Mod(
                    node,
                    nodes.Dict(
                        [
                            nodes.Pair(nodes.Const(key), value)
                            for key, value in variables.items()
                        ]
                    ),
                )
        return nodes.Output([node])


class ExprStmtExtension(Extension):
    """Adds a `do` tag to Jinja that works like the print statement just
    that it doesn't print the return value.
    """

    tags = {"do"}

    def parse(self, parser: "Parser") -> nodes.ExprStmt:
        node = nodes.ExprStmt(lineno=next(parser.stream).lineno)
        node.node = parser.parse_tuple()
        return node


class LoopControlExtension(Extension):
    """Adds break and continue to the template engine."""

    tags = {"break", "continue"}

    def parse(self, parser: "Parser") -> nodes.Break | nodes.Continue:
        token = next(parser.stream)
        if token.value == "break":
            return nodes.Break(lineno=token.lineno)
        return nodes.Continue(lineno=token.lineno)


class DebugExtension(Extension):
    """A ``{% debug %}`` tag that dumps the available variables,
    filters, and tests.

    .. code-block:: html+jinja

        <pre>{% debug %}</pre>

    .. code-block:: text

        {'context': {'cycler': <class 'jinja2.utils.Cycler'>,
                     ...,
                     'namespace': <class 'jinja2.utils.Namespace'>},
         'filters': ['abs', 'attr', 'batch', 'capitalize', 'center', 'count', 'd',
                     ..., 'urlencode', 'urlize', 'wordcount', 'wordwrap', 'xmlattr'],
         'tests': ['!=', '<', '<=', '==', '>', '>=', 'callable', 'defined',
                   ..., 'odd', 'sameas', 'sequence', 'string', 'undefined', 'upper']}

    .. versionadded:: 2.11.0
    """

    tags = {"debug"}

    def parse(self, parser: "Parser") -> nodes.Output:
        lineno = parser.stream.expect("name:debug").lineno
        context = nodes.ContextReference()
        result = self.call_method("_render", [context], lineno=lineno)
        return nodes.Output([result], lineno=lineno)

    def _render(self, context: Context) -> str:
        pass


def extract_from_ast(
    ast: nodes.Template,
    gettext_functions: t.Sequence[str] = GETTEXT_FUNCTIONS,
    babel_style: bool = True,
) -> t.Iterator[tuple[int, str, str | None | tuple[str | None, ...]]]:
    """Extract localizable strings from the given template node.  Per
    default this function returns matches in babel style that means non string
    parameters as well as keyword arguments are returned as `None`.  This
    allows Babel to figure out what you really meant if you are using
    gettext functions that allow keyword arguments for placeholder expansion.
    If you don't want that behavior set the `babel_style` parameter to `False`
    which causes only strings to be returned and parameters are always stored
    in tuples.  As a consequence invalid gettext calls (calls without a single
    string parameter or string parameters after non-string parameters) are
    skipped.

    This example explains the behavior:

    >>> from jinja2 import Environment
    >>> env = Environment()
    >>> node = env.parse('{{ (_("foo"), _(), ngettext("foo", "bar", 42)) }}')
    >>> list(extract_from_ast(node))
    [(1, '_', 'foo'), (1, '_', ()), (1, 'ngettext', ('foo', 'bar', None))]
    >>> list(extract_from_ast(node, babel_style=False))
    [(1, '_', ('foo',)), (1, 'ngettext', ('foo', 'bar'))]

    For every string found this function yields a ``(lineno, function,
    message)`` tuple, where:

    * ``lineno`` is the number of the line on which the string was found,
    * ``function`` is the name of the ``gettext`` function used (if the
      string was extracted from embedded Python code), and
    *   ``message`` is the string, or a tuple of strings for functions
         with multiple string arguments.

    This extraction function operates on the AST and is because of that unable
    to extract any comments.  For comment support you have to use the babel
    extraction interface or extract comments yourself.
    """
    pass


class _CommentFinder:
    """Helper class to find comments in a token stream.  Can only
    find comments for gettext calls forwards.  Once the comment
    from line 4 is found, a comment for line 1 will not return a
    usable value.
    """

    def __init__(
        self, tokens: t.Sequence[tuple[int, str, str]], comment_tags: t.Sequence[str]
    ) -> None:
        self.tokens = tokens
        self.comment_tags = comment_tags
        self.offset = 0
        self.last_lineno = 0

    def find_backwards(self, offset: int) -> list[str]:
        pass

    def find_comments(self, lineno: int) -> list[str]:
        pass


def babel_extract(
    fileobj: t.BinaryIO,
    keywords: t.Sequence[str],
    comment_tags: t.Sequence[str],
    options: dict[str, t.Any],
) -> t.Iterator[tuple[int, str, str | None | tuple[str | None, ...], list[str]]]:
    """Babel extraction method for Jinja templates.

    .. versionchanged:: 2.3
       Basic support for translation comments was added.  If `comment_tags`
       is now set to a list of keywords for extraction, the extractor will
       try to find the best preceding comment that begins with one of the
       keywords.  For best results, make sure to not have more than one
       gettext call in one line of code and the matching comment in the
       same line or the line before.

    .. versionchanged:: 2.5.1
       The `newstyle_gettext` flag can be set to `True` to enable newstyle
       gettext calls.

    .. versionchanged:: 2.7
       A `silent` option can now be provided.  If set to `False` template
       syntax errors are propagated instead of being ignored.

    :param fileobj: the file-like object the messages should be extracted from
    :param keywords: a list of keywords (i.e. function names) that should be
                     recognized as translation functions
    :param comment_tags: a list of translator tags to search for and include
                         in the results.
    :param options: a dictionary of additional options (optional)
    :return: an iterator over ``(lineno, funcname, message, comments)`` tuples.
             (comments will be empty currently)
    """
    pass


#: nicer import names
i18n = InternationalizationExtension
do = ExprStmtExtension
loopcontrols = LoopControlExtension
debug = DebugExtension
