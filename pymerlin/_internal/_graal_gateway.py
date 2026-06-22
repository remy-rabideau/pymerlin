"""
GraalPy drop-in replacement for the py4j gateway object.

Under py4j, Java types are accessed via:
    gateway.jvm.some.package.ClassName

Under GraalPy (running inside the JVM), they are accessed via:
    import java; java.type("some.package.ClassName")

This module provides a GraalGateway object whose .jvm attribute lazily
accumulates dotted name segments and resolves them to Java types via
java.type() — making the rest of _internal/*.py work unchanged.
"""


class _JavaNamespace:
    """
    Lazily accumulates a dotted Java package/class path.
    When attribute access would resolve to a real Java type, returns it.
    Otherwise returns a deeper namespace node.
    """
    def __init__(self, path=""):
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name):
        import java  # GraalPy built-in, available when host access is granted
        full = f"{object.__getattribute__(self, '_path')}.{name}" if object.__getattribute__(self, "_path") else name
        try:
            return java.type(full)
        except Exception:
            return _JavaNamespace(full)

    def __repr__(self):
        return f"<JavaNamespace '{object.__getattribute__(self, '_path')}'>"


class GraalGateway:
    """
    Mimics the py4j JavaGateway interface used throughout pymerlin._internal.

    Usage (inside GraalPy context):
        gateway = GraalGateway()
        Duration = gateway.jvm.gov.nasa.jpl.aerie.merlin.protocol.types.Duration
        arr = gateway.new_array(SomeClass, 3)
    """

    def __init__(self):
        self.jvm = _JavaNamespace()

    def new_array(self, klass, n):
        """
        Equivalent of py4j's gateway.new_array(klass, n).
        Under GraalPy, Java arrays are created via java.type("[L...;") or
        java.type("ClassName[]"), but the simplest universal approach is
        java.lang.reflect.Array.newInstance.
        """
        import java
        Array = java.type("java.lang.reflect.Array")
        # klass here is a GraalPy Java type object; .class gives the Class<?>
        return Array.newInstance(klass.class_, n)

    @property
    def _gateway_client(self):
        """
        py4j uses gateway._gateway_client as a token for MapConverter/ListConverter.
        Under GraalPy, converters are not needed (Python dicts/lists are
        auto-coerced), so this returns a sentinel that the ported
        _serialized_value.py ignores.
        """
        return None
