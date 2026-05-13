class MockYara:
    def compile(self, *args, **kwargs):
        return self
    def match(self, *args, **kwargs):
        return []

def compile(*args, **kwargs):
    return MockYara()
