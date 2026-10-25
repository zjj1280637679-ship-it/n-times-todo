class TodoError(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(code + (": " + detail if detail else ""))
