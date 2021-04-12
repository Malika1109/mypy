"""Plugin for supporting the functools standard library module."""
from typing import Dict, NamedTuple, Optional

import mypy.plugin
from mypy.nodes import ARG_OPT, ARG_POS, ARG_STAR2, Argument, FuncItem, Var
from mypy.plugins.common import add_method_to_class
from mypy.types import AnyType, CallableType, get_proper_type, Type, TypeOfAny, UnboundType


functools_total_ordering_makers = {
    'functools.total_ordering',
}

_ORDERING_METHODS = {
    '__lt__',
    '__le__',
    '__gt__',
    '__ge__',
}


_MethodInfo = NamedTuple('_MethodInfo', [('is_static', bool), ('type', CallableType)])


def functools_total_ordering_maker_callback(ctx: mypy.plugin.ClassDefContext,
                                            auto_attribs_default: bool = False) -> None:
    """Add dunder methods to classes decorated with functools.total_ordering."""
    if ctx.api.options.python_version < (3,):
        ctx.api.fail('"functools.total_ordering" is not supported in Python 2', ctx.reason)
        return

    comparison_methods = _analyze_class(ctx)
    if not comparison_methods:
        ctx.api.fail(
            'No ordering operation defined when using "functools.total_ordering": < > <= >=',
            ctx.reason)
        return

    # prefer __lt__ to __le__ to __gt__ to __ge__
    root = max(comparison_methods, key=lambda k: (comparison_methods[k] is None, k))
    root_method = comparison_methods[root]
    if not root_method:
        # None of the defined comparison methods can be analysed
        return

    other_type = _find_other_type(root_method)
    bool_type = ctx.api.named_type('__builtins__.bool')
    ret_type = bool_type  # type: Type
    if root_method.type.ret_type != ctx.api.named_type('__builtins__.bool'):
        proper_ret_type = get_proper_type(root_method.type.ret_type)
        if not (isinstance(proper_ret_type, UnboundType)
                and proper_ret_type.name.split('.')[-1] == 'bool'):
            ret_type = AnyType(TypeOfAny.implementation_artifact)
    for additional_op in _ORDERING_METHODS:
        # Either the method is not implemented
        # or has an unknown signature that we can now extrapolate.
        if not comparison_methods.get(additional_op):
            args = [Argument(Var('other', other_type), other_type, None, ARG_POS)]
            add_method_to_class(ctx.api, ctx.cls, additional_op, args, ret_type)


def _find_other_type(method: _MethodInfo) -> Type:
    """Find the type of the ``other`` argument in a comparison method."""
    first_arg_pos = 0 if method.is_static else 1
    cur_pos_arg = 0
    other_arg = None
    for arg_kind, arg_type in zip(method.type.arg_kinds, method.type.arg_types):
        if arg_kind in (ARG_POS, ARG_OPT):
            if cur_pos_arg == first_arg_pos:
                other_arg = arg_type
                break

            cur_pos_arg += 1
        elif arg_kind != ARG_STAR2:
            other_arg = arg_type
            break

    if other_arg is None:
        return AnyType(TypeOfAny.implementation_artifact)

    return other_arg


def _analyze_class(ctx: mypy.plugin.ClassDefContext) -> Dict[str, Optional[_MethodInfo]]:
    """Analyze the class body, its parents, and return the comparison methods found."""
    # Traverse the MRO and collect ordering methods.
    comparison_methods = {}  # type: Dict[str, Optional[_MethodInfo]]
    # Skip object because total_ordering does not use methods from object
    for cls in ctx.cls.info.mro[:-1]:
        for name in _ORDERING_METHODS:
            if name in cls.names and name not in comparison_methods:
                node = cls.names[name].node
                if isinstance(node, FuncItem) and isinstance(node.type, CallableType):
                    comparison_methods[name] = _MethodInfo(node.is_static, node.type)
                    continue

                if isinstance(node, Var):
                    proper_type = get_proper_type(node.type)
                    if isinstance(proper_type, CallableType):
                        comparison_methods[name] = _MethodInfo(node.is_staticmethod, proper_type)
                        continue

                comparison_methods[name] = None

    return comparison_methods