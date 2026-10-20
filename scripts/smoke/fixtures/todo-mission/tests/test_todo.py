from todo import TodoList


def test_a_new_list_is_empty():
    assert TodoList().tasks() == []
