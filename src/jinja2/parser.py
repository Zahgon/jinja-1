"""Parse tokens from the lexer into nodes for the compiler."""

import typing
import typing as t

from . import nodes
from .exceptions import TemplateAssertionError
from .exceptions import TemplateSyntaxError
from .lexer import describe_token
from .lexer import describe_token_expr

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .environment import Environment

_ImportInclude = t.TypeVar("_ImportInclude", nodes.Import, nodes.Include)
_MacroCall = t.TypeVar("_MacroCall", nodes.Macro, nodes.CallBlock)

_statement_keywords = frozenset(
    [
        "for",
        "if",
        "block",
        "extends",
        "print",
        "macro",
        "include",
        "from",
        "import",
        "set",
        "with",
        "autoescape",
    ]
)
_compare_operators = frozenset(["eq", "ne", "lt", "lteq", "gt", "gteq"])

_math_nodes: dict[str, type[nodes.Expr]] = {
    "add": nodes.Add,
    "sub": nodes.Sub,
    "mul": nodes.Mul,
    "div": nodes.Div,
    "floordiv": nodes.FloorDiv,
    "mod": nodes.Mod,
}


class Parser:
    """This is the central parsing class Jinja uses.  It's passed to
    extensions and can be used to parse expressions or statements.
    """

    def __init__(
        self,
        environment: "Environment",
        source: str,
        name: str | None = None,
        filename: str | None = None,
        state: str | None = None,
    ) -> None:
        self.environment = environment
        self.stream = environment._tokenize(source, name, filename, state)
        self.name = name
        self.filename = filename
        self.closed = False
        self.extensions: dict[
            str, t.Callable[[Parser], nodes.Node | list[nodes.Node]]
        ] = {}
        for extension in environment.iter_extensions():
            for tag in extension.tags:
                self.extensions[tag] = extension.parse
        self._last_identifier = 0
        self._tag_stack: list[str] = []
        self._end_token_stack: list[tuple[str, ...]] = []

    def fail(
        self,
        msg: str,
        lineno: int | None = None,
        exc: type[TemplateSyntaxError] = TemplateSyntaxError,
    ) -> "te.NoReturn":
        """Convenience method that raises `exc` with the message, passed
        line number or last line number as well as the current name and
        filename.
        """
        if lineno is None:
            lineno = self.stream.current.lineno
        raise exc(msg, lineno, self.name, self.filename)

    def _fail_ut_eof(
        self,
        name: str | None,
        end_token_stack: list[tuple[str, ...]],
        lineno: int | None,
    ) -> "te.NoReturn":
        expected: set[str] = set()
        for exprs in end_token_stack:
            expected.update(map(describe_token_expr, exprs))
        if end_token_stack:
            currently_looking: str | None = " or ".join(
                map(repr, map(describe_token_expr, end_token_stack[-1]))
            )
        else:
            currently_looking = None

        if name is None:
            message = ["Unexpected end of template."]
        else:
            message = [f"Encountered unknown tag {name!r}."]

        if currently_looking:
            if name is not None and name in expected:
                message.append(
                    "You probably made a nesting mistake. Jinja is expecting this tag,"
                    f" but currently looking for {currently_looking}."
                )
            else:
                message.append(
                    f"Jinja was looking for the following tags: {currently_looking}."
                )

        if self._tag_stack:
            message.append(
                "The innermost block that needs to be closed is"
                f" {self._tag_stack[-1]!r}."
            )

        self.fail(" ".join(message), lineno)

    def fail_unknown_tag(self, name: str, lineno: int | None = None) -> "te.NoReturn":
        """Called if the parser encounters an unknown tag.  Tries to fail
        with a human readable error message that could help to identify
        the problem.
        """
        self._fail_ut_eof(name, self._end_token_stack, lineno)

    def fail_eof(
        self,
        end_tokens: tuple[str, ...] | None = None,
        lineno: int | None = None,
    ) -> "te.NoReturn":
        """Like fail_unknown_tag but for end of template situations."""
        stack = list(self._end_token_stack)
        if end_tokens is not None:
            stack.append(end_tokens)
        self._fail_ut_eof(None, stack, lineno)

    def is_tuple_end(self, extra_end_rules: tuple[str, ...] | None = None) -> bool:
        """Are we at the end of a tuple?"""
        if self.stream.current.type in ("variable_end", "block_end", "rparen"):
            return True
        elif extra_end_rules is not None:
            return self.stream.current.test_any(extra_end_rules)  # type: ignore
        return False

    def free_identifier(self, lineno: int | None = None) -> nodes.InternalName:
        """Return a new free identifier as :class:`~jinja2.nodes.InternalName`."""
        pass

    def parse_statement(self) -> nodes.Node | list[nodes.Node]:
        """Parse a single statement."""
        token = self.stream.current
        if token.type != "name":
            self.fail("tag name expected", token.lineno)
        self._tag_stack.append(token.value)
        pop_tag = True
        try:
            if token.value in _statement_keywords:
                f = getattr(self, f"parse_{self.stream.current.value}")
                return f()  # type: ignore
            if token.value == "call":
                return self.parse_call_block()
            if token.value == "filter":
                return self.parse_filter_block()
            ext = self.extensions.get(token.value)
            if ext is not None:
                return ext(self)

            # did not work out, remove the token we pushed by accident
            # from the stack so that the unknown tag fail function can
            # produce a proper error message.
            self._tag_stack.pop()
            pop_tag = False
            self.fail_unknown_tag(token.value, token.lineno)
        finally:
            if pop_tag:
                self._tag_stack.pop()

    def parse_statements(
        self, end_tokens: tuple[str, ...], drop_needle: bool = False
    ) -> list[nodes.Node]:
        """Parse multiple statements into a list until one of the end tokens
        is reached.  This is used to parse the body of statements as it also
        parses template data if appropriate.  The parser checks first if the
        current token is a colon and skips it if there is one.  Then it checks
        for the block end and parses until if one of the `end_tokens` is
        reached.  Per default the active token in the stream at the end of
        the call is the matched end token.  If this is not wanted `drop_needle`
        can be set to `True` and the end token is removed.
        """
        # the first token may be a colon for python compatibility
        self.stream.skip_if("colon")

        # in the future it would be possible to add whole code sections
        # by adding some sort of end of statement token and parsing those here.
        self.stream.expect("block_end")
        result = self.subparse(end_tokens)

        # we reached the end of the template too early, the subparser
        # does not check for this, so we do that now
        if self.stream.current.type == "eof":
            self.fail_eof(end_tokens)

        if drop_needle:
            next(self.stream)
        return result

    def parse_set(self) -> nodes.Assign | nodes.AssignBlock:
        """Parse an assign statement."""
        pass

    def parse_for(self) -> nodes.For:
        """Parse a for loop."""
        pass

    def parse_if(self) -> nodes.If:
        """Parse an if construct."""
        pass

    def parse_with(self) -> nodes.With:
        pass

    def parse_autoescape(self) -> nodes.Scope:
        pass

    def parse_block(self) -> nodes.Block:
        pass

    def parse_extends(self) -> nodes.Extends:
        pass

    def parse_import_context(
        self, node: _ImportInclude, default: bool
    ) -> _ImportInclude:
        pass

    def parse_include(self) -> nodes.Include:
        pass

    def parse_import(self) -> nodes.Import:
        pass

    def parse_from(self) -> nodes.FromImport:
        pass

    def parse_signature(self, node: _MacroCall) -> None:
        args = node.args = []
        defaults = node.defaults = []
        self.stream.expect("lparen")
        while self.stream.current.type != "rparen":
            if args:
                self.stream.expect("comma")
            arg = self.parse_assign_target(name_only=True)
            arg.set_ctx("param")
            if self.stream.skip_if("assign"):
                defaults.append(self.parse_expression())
            elif defaults:
                self.fail("non-default argument follows default argument")
            args.append(arg)
        self.stream.expect("rparen")

    def parse_call_block(self) -> nodes.CallBlock:
        node = nodes.CallBlock(lineno=next(self.stream).lineno)
        if self.stream.current.type == "lparen":
            self.parse_signature(node)
        else:
            node.args = []
            node.defaults = []

        call_node = self.parse_expression()
        if not isinstance(call_node, nodes.Call):
            self.fail("expected call", node.lineno)
        node.call = call_node
        node.body = self.parse_statements(("name:endcall",), drop_needle=True)
        return node

    def parse_filter_block(self) -> nodes.FilterBlock:
        node = nodes.FilterBlock(lineno=next(self.stream).lineno)
        node.filter = self.parse_filter(None, start_inline=True)  # type: ignore
        node.body = self.parse_statements(("name:endfilter",), drop_needle=True)
        return node

    def parse_macro(self) -> nodes.Macro:
        pass

    def parse_print(self) -> nodes.Output:
        pass

    @typing.overload
    def parse_assign_target(
        self, with_tuple: bool = ..., name_only: "te.Literal[True]" = ...
    ) -> nodes.Name: ...

    @typing.overload
    def parse_assign_target(
        self,
        with_tuple: bool = True,
        name_only: bool = False,
        extra_end_rules: tuple[str, ...] | None = None,
        with_namespace: bool = False,
    ) -> nodes.NSRef | nodes.Name | nodes.Tuple: ...

    def parse_assign_target(
        self,
        with_tuple: bool = True,
        name_only: bool = False,
        extra_end_rules: tuple[str, ...] | None = None,
        with_namespace: bool = False,
    ) -> nodes.NSRef | nodes.Name | nodes.Tuple:
        """Parse an assignment target.  As Jinja allows assignments to
        tuples, this function can parse all allowed assignment targets.  Per
        default assignments to tuples are parsed, that can be disable however
        by setting `with_tuple` to `False`.  If only assignments to names are
        wanted `name_only` can be set to `True`.  The `extra_end_rules`
        parameter is forwarded to the tuple parsing function.  If
        `with_namespace` is enabled, a namespace assignment may be parsed.
        """
        target: nodes.Expr

        if name_only:
            token = self.stream.expect("name")
            target = nodes.Name(token.value, "store", lineno=token.lineno)
        else:
            if with_tuple:
                target = self.parse_tuple(
                    simplified=True,
                    extra_end_rules=extra_end_rules,
                    with_namespace=with_namespace,
                )
            else:
                target = self.parse_primary(with_namespace=with_namespace)

            target.set_ctx("store")

        if not target.can_assign():
            self.fail(
                f"can't assign to {type(target).__name__.lower()!r}", target.lineno
            )

        return target  # type: ignore

    def parse_expression(self, with_condexpr: bool = True) -> nodes.Expr:
        """Parse an expression.  Per default all expressions are parsed, if
        the optional `with_condexpr` parameter is set to `False` conditional
        expressions are not parsed.
        """
        if with_condexpr:
            return self.parse_condexpr()
        return self.parse_or()

    def parse_condexpr(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        expr1 = self.parse_or()
        expr3: nodes.Expr | None

        while self.stream.skip_if("name:if"):
            expr2 = self.parse_or()
            if self.stream.skip_if("name:else"):
                expr3 = self.parse_condexpr()
            else:
                expr3 = None
            expr1 = nodes.CondExpr(expr2, expr1, expr3, lineno=lineno)
            lineno = self.stream.current.lineno
        return expr1

    def parse_or(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        left = self.parse_and()
        while self.stream.skip_if("name:or"):
            right = self.parse_and()
            left = nodes.Or(left, right, lineno=lineno)
            lineno = self.stream.current.lineno
        return left

    def parse_and(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        left = self.parse_not()
        while self.stream.skip_if("name:and"):
            right = self.parse_not()
            left = nodes.And(left, right, lineno=lineno)
            lineno = self.stream.current.lineno
        return left

    def parse_not(self) -> nodes.Expr:
        if self.stream.current.test("name:not"):
            lineno = next(self.stream).lineno
            return nodes.Not(self.parse_not(), lineno=lineno)
        return self.parse_compare()

    def parse_compare(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        expr = self.parse_math1()
        ops = []
        while True:
            token_type = self.stream.current.type
            if token_type in _compare_operators:
                next(self.stream)
                ops.append(nodes.Operand(token_type, self.parse_math1()))
            elif self.stream.skip_if("name:in"):
                ops.append(nodes.Operand("in", self.parse_math1()))
            elif self.stream.current.test("name:not") and self.stream.look().test(
                "name:in"
            ):
                self.stream.skip(2)
                ops.append(nodes.Operand("notin", self.parse_math1()))
            else:
                break
            lineno = self.stream.current.lineno
        if not ops:
            return expr
        return nodes.Compare(expr, ops, lineno=lineno)

    def parse_math1(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        left = self.parse_concat()
        while self.stream.current.type in ("add", "sub"):
            cls = _math_nodes[self.stream.current.type]
            next(self.stream)
            right = self.parse_concat()
            left = cls(left, right, lineno=lineno)
            lineno = self.stream.current.lineno
        return left

    def parse_concat(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        args = [self.parse_math2()]
        while self.stream.current.type == "tilde":
            next(self.stream)
            args.append(self.parse_math2())
        if len(args) == 1:
            return args[0]
        return nodes.Concat(args, lineno=lineno)

    def parse_math2(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        left = self.parse_pow()
        while self.stream.current.type in ("mul", "div", "floordiv", "mod"):
            cls = _math_nodes[self.stream.current.type]
            next(self.stream)
            right = self.parse_pow()
            left = cls(left, right, lineno=lineno)
            lineno = self.stream.current.lineno
        return left

    def parse_pow(self) -> nodes.Expr:
        lineno = self.stream.current.lineno
        left = self.parse_unary()
        while self.stream.current.type == "pow":
            next(self.stream)
            right = self.parse_unary()
            left = nodes.Pow(left, right, lineno=lineno)
            lineno = self.stream.current.lineno
        return left

    def parse_unary(self, with_filter: bool = True) -> nodes.Expr:
        token_type = self.stream.current.type
        lineno = self.stream.current.lineno
        node: nodes.Expr

        if token_type == "sub":
            next(self.stream)
            node = nodes.Neg(self.parse_unary(False), lineno=lineno)
        elif token_type == "add":
            next(self.stream)
            node = nodes.Pos(self.parse_unary(False), lineno=lineno)
        else:
            node = self.parse_primary()
        node = self.parse_postfix(node)
        if with_filter:
            node = self.parse_filter_expr(node)
        return node

    def parse_primary(self, with_namespace: bool = False) -> nodes.Expr:
        """Parse a name or literal value. If ``with_namespace`` is enabled, also
        parse namespace attr refs, for use in assignments."""
        token = self.stream.current
        node: nodes.Expr
        if token.type == "name":
            next(self.stream)
            if token.value in ("true", "false", "True", "False"):
                node = nodes.Const(token.value in ("true", "True"), lineno=token.lineno)
            elif token.value in ("none", "None"):
                node = nodes.Const(None, lineno=token.lineno)
            elif with_namespace and self.stream.current.type == "dot":
                # If namespace attributes are allowed at this point, and the next
                # token is a dot, produce a namespace reference.
                next(self.stream)
                attr = self.stream.expect("name")
                node = nodes.NSRef(token.value, attr.value, lineno=token.lineno)
            else:
                node = nodes.Name(token.value, "load", lineno=token.lineno)
        elif token.type == "string":
            next(self.stream)
            buf = [token.value]
            lineno = token.lineno
            while self.stream.current.type == "string":
                buf.append(self.stream.current.value)
                next(self.stream)
            node = nodes.Const("".join(buf), lineno=lineno)
        elif token.type in ("integer", "float"):
            next(self.stream)
            node = nodes.Const(token.value, lineno=token.lineno)
        elif token.type == "lparen":
            next(self.stream)
            node = self.parse_tuple(explicit_parentheses=True)
            self.stream.expect("rparen")
        elif token.type == "lbracket":
            node = self.parse_list()
        elif token.type == "lbrace":
            node = self.parse_dict()
        else:
            self.fail(f"unexpected {describe_token(token)!r}", token.lineno)
        return node

    def parse_tuple(
        self,
        simplified: bool = False,
        with_condexpr: bool = True,
        extra_end_rules: tuple[str, ...] | None = None,
        explicit_parentheses: bool = False,
        with_namespace: bool = False,
    ) -> nodes.Tuple | nodes.Expr:
        """Works like `parse_expression` but if multiple expressions are
        delimited by a comma a :class:`~jinja2.nodes.Tuple` node is created.
        This method could also return a regular expression instead of a tuple
        if no commas where found.

        The default parsing mode is a full tuple.  If `simplified` is `True`
        only names and literals are parsed; ``with_namespace`` allows namespace
        attr refs as well. The `no_condexpr` parameter is forwarded to
        :meth:`parse_expression`.

        Because tuples do not require delimiters and may end in a bogus comma
        an extra hint is needed that marks the end of a tuple.  For example
        for loops support tuples between `for` and `in`.  In that case the
        `extra_end_rules` is set to ``['name:in']``.

        `explicit_parentheses` is true if the parsing was triggered by an
        expression in parentheses.  This is used to figure out if an empty
        tuple is a valid expression or not.
        """
        lineno = self.stream.current.lineno
        if simplified:

            def parse() -> nodes.Expr:
                return self.parse_primary(with_namespace=with_namespace)

        else:

            def parse() -> nodes.Expr:
                return self.parse_expression(with_condexpr=with_condexpr)

        args: list[nodes.Expr] = []
        is_tuple = False

        while True:
            if args:
                self.stream.expect("comma")
            if self.is_tuple_end(extra_end_rules):
                break
            args.append(parse())
            if self.stream.current.type == "comma":
                is_tuple = True
            else:
                break
            lineno = self.stream.current.lineno

        if not is_tuple:
            if args:
                return args[0]

            # if we don't have explicit parentheses, an empty tuple is
            # not a valid expression.  This would mean nothing (literally
            # nothing) in the spot of an expression would be an empty
            # tuple.
            if not explicit_parentheses:
                self.fail(
                    "Expected an expression,"
                    f" got {describe_token(self.stream.current)!r}"
                )

        return nodes.Tuple(args, "load", lineno=lineno)

    def parse_list(self) -> nodes.List:
        token = self.stream.expect("lbracket")
        items: list[nodes.Expr] = []
        while self.stream.current.type != "rbracket":
            if items:
                self.stream.expect("comma")
            if self.stream.current.type == "rbracket":
                break
            items.append(self.parse_expression())
        self.stream.expect("rbracket")
        return nodes.List(items, lineno=token.lineno)

    def parse_dict(self) -> nodes.Dict:
        token = self.stream.expect("lbrace")
        items: list[nodes.Pair] = []
        while self.stream.current.type != "rbrace":
            if items:
                self.stream.expect("comma")
            if self.stream.current.type == "rbrace":
                break
            key = self.parse_expression()
            self.stream.expect("colon")
            value = self.parse_expression()
            items.append(nodes.Pair(key, value, lineno=key.lineno))
        self.stream.expect("rbrace")
        return nodes.Dict(items, lineno=token.lineno)

    def parse_postfix(self, node: nodes.Expr) -> nodes.Expr:
        pass

    def parse_filter_expr(self, node: nodes.Expr) -> nodes.Expr:
        pass

    def parse_subscript(self, node: nodes.Expr) -> nodes.Getattr | nodes.Getitem:
        pass

    def parse_subscribed(self) -> nodes.Expr:
        pass

    def parse_call_args(
        self,
    ) -> tuple[
        list[nodes.Expr],
        list[nodes.Keyword],
        nodes.Expr | None,
        nodes.Expr | None,
    ]:
        token = self.stream.expect("lparen")
        args = []
        kwargs = []
        dyn_args = None
        dyn_kwargs = None
        require_comma = False

        def ensure(expr: bool) -> None:
            if not expr:
                self.fail("invalid syntax for function call expression", token.lineno)

        while self.stream.current.type != "rparen":
            if require_comma:
                self.stream.expect("comma")

                # support for trailing comma
                if self.stream.current.type == "rparen":
                    break

            if self.stream.current.type == "mul":
                ensure(dyn_args is None and dyn_kwargs is None)
                next(self.stream)
                dyn_args = self.parse_expression()
            elif self.stream.current.type == "pow":
                ensure(dyn_kwargs is None)
                next(self.stream)
                dyn_kwargs = self.parse_expression()
            else:
                if (
                    self.stream.current.type == "name"
                    and self.stream.look().type == "assign"
                ):
                    # Parsing a kwarg
                    ensure(dyn_kwargs is None)
                    key = self.stream.current.value
                    self.stream.skip(2)
                    value = self.parse_expression()
                    kwargs.append(nodes.Keyword(key, value, lineno=value.lineno))
                else:
                    # Parsing an arg
                    ensure(dyn_args is None and dyn_kwargs is None and not kwargs)
                    args.append(self.parse_expression())

            require_comma = True

        self.stream.expect("rparen")
        return args, kwargs, dyn_args, dyn_kwargs

    def parse_call(self, node: nodes.Expr) -> nodes.Call:
        # The lparen will be expected in parse_call_args, but the lineno
        # needs to be recorded before the stream is advanced.
        pass

    def parse_filter(
        self, node: nodes.Expr | None, start_inline: bool = False
    ) -> nodes.Expr | None:
        while self.stream.current.type == "pipe" or start_inline:
            if not start_inline:
                next(self.stream)
            token = self.stream.expect("name")
            name = token.value
            while self.stream.current.type == "dot":
                next(self.stream)
                name += "." + self.stream.expect("name").value
            if self.stream.current.type == "lparen":
                args, kwargs, dyn_args, dyn_kwargs = self.parse_call_args()
            else:
                args = []
                kwargs = []
                dyn_args = dyn_kwargs = None
            node = nodes.Filter(
                node, name, args, kwargs, dyn_args, dyn_kwargs, lineno=token.lineno
            )
            start_inline = False
        return node

    def parse_test(self, node: nodes.Expr) -> nodes.Expr:
        pass

    def subparse(self, end_tokens: tuple[str, ...] | None = None) -> list[nodes.Node]:
        body: list[nodes.Node] = []
        data_buffer: list[nodes.Node] = []
        add_data = data_buffer.append

        if end_tokens is not None:
            self._end_token_stack.append(end_tokens)

        def flush_data() -> None:
            if data_buffer:
                lineno = data_buffer[0].lineno
                body.append(nodes.Output(data_buffer[:], lineno=lineno))
                del data_buffer[:]

        try:
            while self.stream:
                token = self.stream.current
                if token.type == "data":
                    if token.value:
                        add_data(nodes.TemplateData(token.value, lineno=token.lineno))
                    next(self.stream)
                elif token.type == "variable_begin":
                    next(self.stream)
                    add_data(self.parse_tuple(with_condexpr=True))
                    self.stream.expect("variable_end")
                elif token.type == "block_begin":
                    flush_data()
                    next(self.stream)
                    if end_tokens is not None and self.stream.current.test_any(
                        *end_tokens
                    ):
                        return body
                    rv = self.parse_statement()
                    if isinstance(rv, list):
                        body.extend(rv)
                    else:
                        body.append(rv)
                    self.stream.expect("block_end")
                else:
                    raise AssertionError("internal parsing error")

            flush_data()
        finally:
            if end_tokens is not None:
                self._end_token_stack.pop()
        return body

    def parse(self) -> nodes.Template:
        """Parse the whole template into a `Template` node."""
        result = nodes.Template(self.subparse(), lineno=1)
        result.set_environment(self.environment)
        return result
