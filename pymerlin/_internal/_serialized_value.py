def _is_graal_gateway(gateway):
    from pymerlin._internal._graal_gateway import GraalGateway
    return isinstance(gateway, GraalGateway)


def _to_java_map(gateway, py_dict):
    """Convert a Python dict to a Java Map, using the appropriate bridge."""
    if _is_graal_gateway(gateway):
        import java
        HashMap = java.type("java.util.HashMap")
        m = HashMap()
        for k, v in py_dict.items():
            m.put(k, v)
        return m
    else:
        from py4j.java_collections import MapConverter
        return MapConverter().convert(py_dict, gateway._gateway_client)


def _to_java_list(gateway, py_list):
    """Convert a Python list to a Java List, using the appropriate bridge."""
    if _is_graal_gateway(gateway):
        import java
        ArrayList = java.type("java.util.ArrayList")
        lst = ArrayList()
        for item in py_list:
            lst.add(item)
        return lst
    else:
        from py4j.java_collections import ListConverter
        return ListConverter().convert(py_list, gateway._gateway_client)


def _is_java_map(gateway, value):
    if _is_graal_gateway(gateway):
        import java
        return isinstance(value, java.type("java.util.Map"))
    else:
        from py4j.java_collections import JavaMap
        return isinstance(value, JavaMap)


def _is_java_list(gateway, value):
    if _is_graal_gateway(gateway):
        import java
        return isinstance(value, java.type("java.util.List"))
    else:
        from py4j.java_collections import JavaList
        return isinstance(value, JavaList)


def from_serialized_value(gateway, value):
    return value.match(SerializedValueVisitor(gateway))


class SerializedValueVisitor:
    def __init__(self, gateway):
        self.gateway = gateway

    def onNull(self, value):
        return None

    def onNumeric(self, value):
        return float(value)

    def onBoolean(self, value):
        return bool(value)

    def onString(self, value):
        return str(value)

    def onMap(self, value):
        return {k: from_serialized_value(self.gateway, v) for k, v in value.items()}

    def onList(self, value):
        return [from_serialized_value(self.gateway, v) for v in value]

    class Java:
        implements = ["gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue$Visitor"]


def to_serialized_value(gateway, value):
    SerializedValue = gateway.jvm.gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue
    if type(value) is str:
        return SerializedValue.of(value)
    if type(value) is int:
        return SerializedValue.of(value)
    if type(value) is float:
        return SerializedValue.of(value)
    if type(value) is dict or _is_java_map(gateway, value):
        return SerializedValue.of(
            _to_java_map(gateway, {k: to_serialized_value(gateway, v) for k, v in value.items()}))
    if type(value) is list or _is_java_list(gateway, value):
        return SerializedValue.of(
            _to_java_list(gateway, [to_serialized_value(gateway, v) for v in value]))
    raise NotImplementedError(value)


def to_map_str_serialized_value(gateway, dictionary):
    if type(dictionary) != dict:
        raise ValueError("dictionary must be a dict, was " + str(type(dictionary)))
    return {
        k: to_serialized_value(gateway, v)
        for k, v in dictionary.items()
    }


def from_map_str_serialized_value(gateway, dictionary):
    return {
        k: from_serialized_value(gateway, v)
        for k, v in dictionary.items()
    }
