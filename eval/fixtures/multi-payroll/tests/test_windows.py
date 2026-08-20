from payroll import windows


def test_rolling_includes_the_final_window():
    assert len(windows.rolling([1, 2, 3], 2)) == 2
