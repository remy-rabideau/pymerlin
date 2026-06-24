"""
Python runtime server for PyMerlin code generation approach.

This module is launched as a subprocess by the generated Java mission model JAR.
It connects to the Java GatewayServer via py4j and registers a bridge object
that Java can call to execute Python activity functions.
"""

import argparse
import importlib.util
import os
import sys
from py4j.java_gateway import JavaGateway, GatewayParameters


class PyMerlinBridge:
    """
    Bridge object that Java calls into for activity execution.
    This is registered with the Java GatewayServer entry point.
    """
    
    def __init__(self, model_class, gateway):
        self.model_class = model_class
        self.gateway = gateway
        self.model_instance = None
        
    def runActivity(self, activity_name, args):
        """
        Execute a Python activity function.
        
        Args:
            activity_name: Name of the activity (e.g. "activity1")
            args: Map<String, Object> of activity parameters
        
        Returns:
            Activity return value (or None)
        """
        print(f"[PyMerlinBridge] Running activity: {activity_name}")
        
        # Get the activity function from the model class
        if not hasattr(self.model_class, 'activity_types'):
            raise ValueError(f"Model class has no activity_types: {self.model_class}")
        
        if activity_name not in self.model_class.activity_types:
            raise ValueError(f"Unknown activity: {activity_name}")
        
        task_def = self.model_class.activity_types[activity_name]
        
        # Initialize model if needed
        if self.model_instance is None:
            from pymerlin._internal._registrar import Registrar
            registrar = Registrar()
            self.model_instance = self.model_class(registrar)
        
        # Convert Java Map to Python dict
        py_args = {}
        if args is not None:
            for key in args.keySet():
                py_args[key] = args.get(key)
        
        # Execute the activity function
        # The activity function signature is: activity_func(mission, **kwargs)
        result = task_def.inner(self.model_instance, **py_args)
        
        return result
    
    class Java:
        implements = ["gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime$PyMerlinBridge"]


def load_model_class(model_ref):
    """Load a model class from 'path/to/file.py:ClassName'."""
    if ":" not in model_ref:
        raise ValueError(f"model_ref must be 'path/to/file.py:ClassName', got: {model_ref!r}")
    
    file_path, class_name = model_ref.rsplit(":", 1)
    file_path = os.path.abspath(file_path)
    
    spec = importlib.util.spec_from_file_location("_pymerlin_user_model", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(file_path))
    spec.loader.exec_module(module)
    
    return getattr(module, class_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Model reference (path/to/model.py:ClassName)")
    parser.add_argument("--gateway-port", type=int, required=True, help="Java gateway port")
    args = parser.parse_args()
    
    print(f"[PyMerlinServer] Loading model: {args.model}")
    model_class = load_model_class(args.model)
    
    print(f"[PyMerlinServer] Connecting to Java gateway on port {args.gateway_port}")
    gateway = JavaGateway(gateway_parameters=GatewayParameters(port=args.gateway_port, auto_convert=True))
    
    # Create bridge and register with Java
    bridge = PyMerlinBridge(model_class, gateway)
    
    print("[PyMerlinServer] Registering bridge with Java")
    entry_point = gateway.entry_point
    entry_point.register(bridge)
    
    print("[PyMerlinServer] Ready to execute activities")
    
    # Keep the process alive
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[PyMerlinServer] Shutting down")
        gateway.shutdown()


if __name__ == "__main__":
    main()
