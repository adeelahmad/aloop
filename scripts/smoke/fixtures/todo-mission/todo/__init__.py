"""todo: a small to-do list."""


class TodoList:
    """The tasks a user has added to their list, oldest first."""

    def __init__(self):
        self._items = []

    def add(self, text):
        """Add a task to the list."""
        pass

    def tasks(self):
        """The tasks in the list, oldest first."""
        return list(self._items)
