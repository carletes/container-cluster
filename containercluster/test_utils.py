from pytest import raises

from containercluster import utils


def test_multiple_error_message():
    err = utils.MultipleError(KeyError("foo"), ValueError("bar"))
    assert str(err) == "'foo'\nbar"


def test_errors_in_parallel():
    def divide_by_zero(n):
        return n / 0

    def key_error(k):
        return {}[k]

    try:
        divide_by_zero(42)
    except ZeroDivisionError as err:
        divide_by_zero_err = str(err)

    with raises(utils.MultipleError) as err:
        utils.parallel(((utils.run, "echo foo"),
                        (key_error, "no-such-key"),
                        (divide_by_zero, 42),
                        (utils.run, "echo bar")))
    errors = set(str(exc) for exc in err.value)
    assert errors == set(("'no-such-key'", divide_by_zero_err))
