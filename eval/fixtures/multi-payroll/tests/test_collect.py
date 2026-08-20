from payroll import collect


def test_gather_does_not_leak_between_calls():
    collect.gather([1])
    assert collect.gather([2]) == [2]
