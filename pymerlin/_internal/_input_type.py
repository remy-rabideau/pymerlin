class InputType:
    def __init__(self, gateway=None):
        self._gateway = gateway

    def _jlist(self):
        if self._gateway:
            return self._gateway.jvm.java.util.Collections.emptyList()
        return []

    def _jmap(self):
        if self._gateway:
            return self._gateway.jvm.java.util.HashMap()
        return {}

    def getParameters(self):
        return self._jlist()

    def getRequiredParameters(self):
        return self._jlist()

    def instantiate(self, args):
        return self._jmap()

    def getArguments(self, val):
        return self._jmap()

    def getValidationFailures(self, val):
        return self._jlist()

    def validateArguments(self, arguments):
        return self.getValidationFailures(self.instantiate(arguments))

    def getEffectiveArguments(self, arguments):
        return self.getArguments(self.instantiate(arguments))

    class Java:
        implements = ["gov.nasa.jpl.aerie.merlin.protocol.model.InputType"]
