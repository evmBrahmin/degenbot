from . import yul_operations as yul


def divRoundingUp(x, y):
    return yul.add(yul.div(x, y), yul.gt(yul.mod(x, y), 0))
