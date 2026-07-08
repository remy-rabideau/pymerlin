"""
Provide the MissionModel decorator, which generates the .ActivityType decorators on the decorated class.
"""

import inspect
import warnings

from pymerlin._internal._task_specification import TaskInstance


def MissionModel(cls):
    """
    Decorate a class
    :param cls:
    :return:
    """
    if not inspect.isclass(cls):
        warnings.warn("@MissionModel decorator is intended to be used on classes")

    cls.activity_types = {}

    def ActivityType(func):
        if type(func) == TaskDefinition:
            activity_definition = func
        elif callable(func):
            activity_definition = TaskDefinition(func.__name__, func)
            activity_definition.raw_func = func
        else:
            raise ValueError("Cannot decorate " + repr(func) + " with @ActivityType")
        if activity_definition.name in cls.activity_types:
            warnings.warn("Re-defining activity type: " + activity_definition.name)
        cls.activity_types[activity_definition.name] = activity_definition
        return activity_definition
    cls.ActivityType = ActivityType
    return cls


class TaskDefinition:
    """
    TaskDefinition can produce a TaskInstance given all of the arguments for that task.
    """
    def __init__(self, name, func):
        self.name = name
        self.inner = func
        self.raw_func = func

    def __call__(self, *args, **kwargs):
        return self.make_instance(*args, **kwargs)

    def make_instance(self, *args, **kwargs) -> TaskInstance:
        instance = TaskInstance(lambda: self.inner.__call__(*args, **kwargs))
        instance.activity_name = self.name
        return instance