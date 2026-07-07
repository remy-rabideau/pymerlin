"""
Java code generator for PyMerlin models.

Introspects a Python Mission class and generates Java source files that implement
the Aerie ModelType interface, with one ActivityMapper per activity type.
"""

import inspect
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Look for .env in the pymerlin package root
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass  # python-dotenv not installed, will use defaults


def generate_mission_model_jar(model_class, model_name: str, model_version: str, output_jar: str, model_ref: str = None):
    """
    Generate a complete Aerie-compatible mission model JAR from a Python model class.
    
    Args:
        model_class: The Python Mission class decorated with @MissionModel
        model_name: Model name (e.g. "my-model")
        model_version: Model version (e.g. "1.0.0")
        output_jar: Output path for the generated JAR file
        model_ref: Model reference string (e.g. "demo/model.py:Mission")
    """
    
    # Extract metadata from the model class
    activity_types = _extract_activity_types(model_class)
    resources = _extract_resources(model_class)
    
    # Parse model_ref to get the Python file path and class name
    if model_ref and ":" in model_ref:
        model_file_path, class_name = model_ref.rsplit(":", 1)
        model_file = Path(model_file_path).resolve()
        # Update model_ref to use bundled path in JAR
        bundled_model_ref = f"/pymerlin_models/{model_file.name}:{class_name}"
    else:
        model_file = None
        bundled_model_ref = model_ref
    
    # Generate Java source files
    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = Path(tmpdir) / "src" / "main" / "java"
        pkg_dir = src_dir / "gov" / "nasa" / "ammos" / "aerie" / "pymerlin" / "generated"
        pkg_dir.mkdir(parents=True)
        
        # Generate the main plugin class
        _generate_plugin_class(pkg_dir, model_name, model_version)
        
        # Generate the ModelType implementation
        _generate_model_type_class(pkg_dir, model_name, activity_types, resources, bundled_model_ref)
        
        # Generate ActivityTypes registry
        _generate_activity_types_class(pkg_dir, activity_types)
        
        # Generate one ActivityMapper per activity
        for activity_name, activity_info in activity_types.items():
            _generate_activity_mapper(pkg_dir, activity_name, activity_info)
        
        # Compile Java sources
        _compile_java_sources(src_dir, tmpdir)
        
        # Package into JAR
        _package_jar(tmpdir, output_jar, model_name, model_version, model_file)


def _extract_activity_types(model_class):
    """Extract activity type metadata from the model class."""
    activities = {}
    
    if not hasattr(model_class, 'activity_types'):
        return activities
    
    for name, task_def in model_class.activity_types.items():
        # Get the original function to inspect parameters
        func = task_def.inner
        sig = inspect.signature(func)
        
        params = {}
        for param_name, param in sig.parameters.items():
            if param_name == 'mission':  # Skip the model parameter
                continue
            
            param_type = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default_value = param.default if param.default != inspect.Parameter.empty else None
            
            params[param_name] = {
                'type': param_type,
                'default': default_value,
                'required': param.default == inspect.Parameter.empty
            }
        
        activities[name] = {
            'params': params,
            'func': func
        }
    
    return activities


def _extract_resources(model_class):
    """Extract resource metadata by instantiating the model with a mock registrar."""
    from pymerlin._internal._registrar import Registrar
    
    registrar = Registrar()
    
    # Instantiate the model to capture resource registrations
    try:
        model_class(registrar)
    except Exception:
        pass  # Ignore errors during introspection
    
    resources = {}
    for resource_name, resource_func in registrar.resources:
        resources[resource_name] = {
            'name': resource_name,
            'type': 'discrete'  # For now, all resources are discrete
        }
    
    return resources


def _generate_plugin_class(pkg_dir: Path, model_name: str, model_version: str):
    """Generate the MerlinPlugin implementation."""
    
    code = '''package gov.nasa.ammos.aerie.pymerlin.generated;

import gov.nasa.jpl.aerie.merlin.protocol.model.MerlinPlugin;

public final class GeneratedMerlinPlugin implements MerlinPlugin {
    @Override
    public GeneratedModelType getModelType() {
        return new GeneratedModelType();
    }
}
'''
    
    (pkg_dir / "GeneratedMerlinPlugin.java").write_text(code)


def _generate_model_type_class(pkg_dir: Path, model_name: str, activity_types: dict, resources: dict, model_ref: str = None):
    """Generate the ModelType implementation."""
    
    model_ref_literal = model_ref if model_ref else "model.py:Mission"

    # Build field declarations for resource topics (one Topic<String> per resource)
    resource_fields = []
    resource_registrations = []
    for resource_name in resources.keys():
        safe_name = resource_name.replace("/", "_").replace("-", "_").lstrip("_")
        resource_fields.append(
            f'    private final gov.nasa.jpl.aerie.merlin.protocol.driver.Topic<String> topic_{safe_name} = new gov.nasa.jpl.aerie.merlin.protocol.driver.Topic<>();'
        )
        resource_registrations.append(f'''
        // Allocate a cell for {resource_name} backed by topic_{safe_name}.
        // State is String[] of length 1 (mutable holder); effect is the new String value.
        final String[] init_{safe_name} = new String[]{{
            (runtimeFinal != null) ? (String) runtimeFinal.getBridge().getResource("{resource_name}") : ""
        }};
        final gov.nasa.jpl.aerie.merlin.protocol.driver.CellId<String[]> cell_{safe_name} =
            builder.allocate(
                init_{safe_name},
                new gov.nasa.jpl.aerie.merlin.protocol.model.CellType<String, String[]>() {{
                    @Override public gov.nasa.jpl.aerie.merlin.protocol.model.EffectTrait<String> getEffectType() {{
                        return new gov.nasa.jpl.aerie.merlin.protocol.model.EffectTrait<String>() {{
                            @Override public String empty() {{ return null; }}
                            @Override public String sequentially(String prefix, String suffix) {{ return suffix != null ? suffix : prefix; }}
                            @Override public String concurrently(String left, String right) {{ return right != null ? right : left; }}
                        }};
                    }}
                    @Override public void apply(String[] state, String effect) {{ if (effect != null) state[0] = effect; }}
                    @Override public String[] duplicate(String[] state) {{ return new String[]{{state[0]}}; }}
                }},
                event -> event,
                this.topic_{safe_name});
        builder.resource("{resource_name}", new gov.nasa.jpl.aerie.merlin.protocol.model.Resource<String>() {{
            @Override public String getType() {{ return "discrete"; }}
            @Override public gov.nasa.jpl.aerie.merlin.protocol.model.OutputType<String> getOutputType() {{
                return new gov.nasa.jpl.aerie.merlin.protocol.model.OutputType<String>() {{
                    @Override public gov.nasa.jpl.aerie.merlin.protocol.types.ValueSchema getSchema() {{
                        return gov.nasa.jpl.aerie.merlin.protocol.types.ValueSchema.STRING;
                    }}
                    @Override public SerializedValue serialize(String value) {{
                        return SerializedValue.of(value);
                    }}
                }};
            }}
            @Override public String getDynamics(gov.nasa.jpl.aerie.merlin.protocol.driver.Querier querier) {{
                return querier.getState(cell_{safe_name})[0];
            }}
        }});
        // Register a CellEmitter so Python can push new values to Aerie via ModelActions.emit.
        // Guarded by null check (Python unavailable during resource extraction on merlin server).
        if (runtimeFinal != null) {{
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.CellEmitter emitter_{safe_name} = value ->
                gov.nasa.jpl.aerie.merlin.framework.ModelActions.emit(value, this.topic_{safe_name});
            runtimeFinal.getBridge().registerCellEmitter("{resource_name}", emitter_{safe_name});
            gov.nasa.ammos.aerie.pymerlin.generated.ActivityTypes.cellEmitters.put("{resource_name}", emitter_{safe_name});
        }}''')

    resource_field_block = "\n".join(resource_fields)
    resource_block = "\n".join(resource_registrations)

    code = f'''package gov.nasa.ammos.aerie.pymerlin.generated;

import gov.nasa.jpl.aerie.merlin.framework.ActivityMapper;
import gov.nasa.jpl.aerie.merlin.protocol.driver.Initializer;
import gov.nasa.jpl.aerie.merlin.protocol.model.InputType;
import gov.nasa.jpl.aerie.merlin.protocol.model.ModelType;
import gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue;
import gov.nasa.jpl.aerie.merlin.protocol.types.Unit;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public final class GeneratedModelType implements ModelType<Unit, Unit> {{
{resource_field_block}

    @Override
    public Map<String, ActivityMapper<Unit, ?, ?>> getDirectiveTypes() {{
        return ActivityTypes.directiveTypes;
    }}
    
    @Override
    public List<String> getSubsystems() {{
        return List.of();
    }}
    
    @Override
    public InputType<Unit> getConfigurationType() {{
        return new InputType<Unit>() {{
            @Override
            public List<Parameter> getParameters() {{
                return List.of();
            }}
            
            @Override
            public List<String> getRequiredParameters() {{
                return List.of();
            }}
            
            @Override
            public Unit instantiate(Map<String, SerializedValue> arguments) {{
                return Unit.UNIT;
            }}
            
            @Override
            public Map<String, SerializedValue> getArguments(Unit value) {{
                return Map.of();
            }}
            
            @Override
            public List<ValidationNotice> getValidationFailures(Unit value) {{
                return List.of();
            }}
        }};
    }}
    
    @Override
    public Unit instantiate(Instant planStart, Unit configuration, Initializer builder) {{
        System.setProperty("pymerlin.model.ref", "{model_ref_literal}");

        ActivityTypes.registerTopics(builder);

        gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime runtime = null;
        try {{
            runtime = gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.getInstance(
                System.getProperty("pymerlin.model.ref", "{model_ref_literal}"));
        }} catch (Exception e) {{
            System.err.println("[PyMerlin] Python runtime unavailable during instantiate (resource extraction?): " + e.getMessage());
        }}

        final gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime runtimeFinal = runtime;

{resource_block}
        return Unit.UNIT;
    }}
}}
'''
    
    (pkg_dir / "GeneratedModelType.java").write_text(code)


def _generate_activity_types_class(pkg_dir: Path, activity_types: dict):
    """Generate the ActivityTypes registry."""
    
    mapper_instances = []
    map_entries = []
    
    for activity_name in activity_types.keys():
        mapper_class = f"{_to_java_class_name(activity_name)}Mapper"
        mapper_var = f"mapper_{activity_name}"
        
        mapper_instances.append(f"    public static final {mapper_class} {mapper_var} = new {mapper_class}();")
        map_entries.append(f'        Map.entry("{activity_name}", {mapper_var})')
    
    code = f'''package gov.nasa.ammos.aerie.pymerlin.generated;

import gov.nasa.jpl.aerie.merlin.framework.ActivityMapper;
import gov.nasa.jpl.aerie.merlin.protocol.driver.Initializer;
import gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory;
import gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue;
import gov.nasa.jpl.aerie.merlin.protocol.types.Unit;

import java.util.Map;

public final class ActivityTypes {{
{chr(10).join(mapper_instances)}
    
    public static final Map<String, ActivityMapper<Unit, ?, ?>> directiveTypes = Map.ofEntries(
{("," + chr(10)).join(map_entries)}
    );
    
    public static void registerTopics(Initializer initializer) {{
        directiveTypes.forEach((name, mapper) -> registerDirectiveType(initializer, name, mapper));
    }}

    public static final java.util.Map<String, gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.CellEmitter> cellEmitters =
        new java.util.concurrent.ConcurrentHashMap<>();

    public static gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory<?> getTaskFactory(String name) {{
        ActivityMapper<Unit, ?, ?> mapper = directiveTypes.get(name);
        if (mapper == null) return null;
        return getTaskFactoryTyped(mapper);
    }}

    @SuppressWarnings("unchecked")
    private static <I, O> gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory<O> getTaskFactoryTyped(
            ActivityMapper<Unit, I, O> mapper) {{
        return mapper.getTaskFactory(Unit.UNIT, (I) new java.util.HashMap<String, gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue>());
    }}
    
    private static <Input, Output> void registerDirectiveType(
            Initializer initializer,
            String name,
            ActivityMapper<Unit, Input, Output> mapper) {{
        initializer.topic("ActivityType.Input." + name, mapper.getInputTopic(), mapper.getInputAsOutput());
        initializer.topic("ActivityType.Output." + name, mapper.getOutputTopic(), mapper.getOutputType());
    }}
}}
'''
    
    (pkg_dir / "ActivityTypes.java").write_text(code)


def _generate_activity_mapper(pkg_dir: Path, activity_name: str, activity_info: dict):
    """Generate an ActivityMapper for a single activity type."""
    
    class_name = f"{_to_java_class_name(activity_name)}Mapper"
    params = activity_info['params']
    
    # Generate parameter fields and initialization
    param_fields = []
    param_list = []
    param_getters = []
    # param_setters = []
    
    for param_name, param_info in params.items():
        java_type = _python_type_to_java(param_info['type'])
        param_fields.append(f"    private final ValueMapper<{java_type}> mapper_{param_name};")
        param_list.append(f'        parameters.add(new InputType.Parameter("{param_name}", this.mapper_{param_name}.getValueSchema()));')
        param_getters.append(f'        arguments.put("{param_name}", this.mapper_{param_name}.serializeValue(input.{param_name}));')
    
    code = f'''package gov.nasa.ammos.aerie.pymerlin.generated;

import gov.nasa.jpl.aerie.contrib.serialization.rulesets.BasicValueMappers;
import gov.nasa.jpl.aerie.merlin.framework.ActivityMapper;
import gov.nasa.jpl.aerie.merlin.framework.ModelActions;
import gov.nasa.jpl.aerie.merlin.framework.ValueMapper;
import gov.nasa.jpl.aerie.merlin.protocol.driver.Topic;
import gov.nasa.jpl.aerie.merlin.protocol.model.InputType;
import gov.nasa.jpl.aerie.merlin.protocol.model.OutputType;
import gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory;
import gov.nasa.jpl.aerie.merlin.protocol.types.InstantiationException;
import gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue;
import gov.nasa.jpl.aerie.merlin.protocol.types.Unit;
import gov.nasa.jpl.aerie.merlin.protocol.types.ValueSchema;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

public final class {class_name} implements ActivityMapper<Unit, Map<String, SerializedValue>, Unit> {{
    private final Topic<Map<String, SerializedValue>> inputTopic = new Topic<>();
    private final Topic<Unit> outputTopic = new Topic<>();
    
    @Override
    public InputType<Map<String, SerializedValue>> getInputType() {{
        return new InputMapper();
    }}
    
    @Override
    public OutputType<Unit> getOutputType() {{
        return new OutputMapper();
    }}
    
    @Override
    public Optional<String> getSubsystem() {{
        return Optional.empty();
    }}
    
    @Override
    public Optional<String> getDescription() {{
        return Optional.empty();
    }}
    
    @Override
    public Topic<Map<String, SerializedValue>> getInputTopic() {{
        return this.inputTopic;
    }}
    
    @Override
    public Topic<Unit> getOutputTopic() {{
        return this.outputTopic;
    }}
    
    @Override
    public TaskFactory<Unit> getTaskFactory(Unit model, Map<String, SerializedValue> activity) {{
        return ModelActions.threaded(() -> {{
            ModelActions.emit(activity, this.inputTopic);

            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime runtime =
                gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.getInstance(
                    System.getProperty("pymerlin.model.ref", "model.py:Mission")
                );

            Map<String, Object> pyArgs = new HashMap<>();
            for (Map.Entry<String, SerializedValue> entry : activity.entrySet()) {{
                pyArgs.put(entry.getKey(), deserializeValue(entry.getValue()));
            }}

            // Set classloader so py4j resolves TaskHandle via the mission model URLClassLoader
            Thread t = Thread.currentThread();
            ClassLoader prev = t.getContextClassLoader();
            t.setContextClassLoader(runtime.getClassLoader());
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.TaskHandle handle;
            try {{
                handle = runtime.getBridge().startActivity("{activity_name}", pyArgs);
            }} finally {{
                t.setContextClassLoader(prev);
            }}

            // Drive the Python activity to completion, honouring each delay() and spawn().
            driveHandle(handle, gov.nasa.ammos.aerie.pymerlin.generated.ActivityTypes.cellEmitters);

            ModelActions.emit(Unit.UNIT, this.outputTopic);
            return Unit.UNIT;
        }});
    }}
    
    private Object deserializeValue(SerializedValue sv) {{
        // Simple deserialization for basic types - just pass through for now
        // TODO: Properly deserialize to Python-compatible types
        return sv;
    }}

    private static void driveHandle(
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.TaskHandle h,
            java.util.Map<String, gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.CellEmitter> emitters) {{
        drainEmits(h, emitters);
        spawnChildren(h);
        while (!"completed".equals(h.getStatus())) {{
            if ("delayed".equals(h.getStatus())) {{
                long micros = h.getDelayMicros();
                ModelActions.delay(
                    gov.nasa.jpl.aerie.merlin.protocol.types.Duration.of(
                        micros,
                        gov.nasa.jpl.aerie.merlin.protocol.types.Duration.MICROSECONDS));
            }}
            h.step();
            drainEmits(h, emitters);
            spawnChildren(h);
        }}
    }}

    private static void drainEmits(
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.TaskHandle h,
            java.util.Map<String, gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.CellEmitter> emitters) {{
        String resourceName;
        while ((resourceName = h.getPendingEmitResource()) != null) {{
            String value = h.getPendingEmitValue();
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.CellEmitter emitter = emitters.get(resourceName);
            if (emitter != null) {{
                emitter.emit(value);
            }}
        }}
    }}

    @SuppressWarnings("unchecked")
    private static void spawnChildren(gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.TaskHandle parent) {{
        ClassLoader mcl = gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.class.getClassLoader();
        Thread ct = Thread.currentThread();
        ClassLoader prev = ct.getContextClassLoader();
        // Keep CCL set for the entire drain loop: SpawnInfo is a py4j proxy and
        // needs the mission model URLClassLoader for interface resolution.
        ct.setContextClassLoader(mcl);
        try {{
            gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime.SpawnInfo info;
            while ((info = parent.getSpawnedHandle()) != null) {{
                final String childName = info.getName();
                System.out.println("[SpawnChildren] Spawning child activity: " + childName);
                // Look up the child's mapper so getTaskFactory emits to its input/output topics,
                // which is what makes Aerie record the span as a named simulated activity.
                gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory<?> childTask =
                    gov.nasa.ammos.aerie.pymerlin.generated.ActivityTypes.getTaskFactory(childName);
                if (childTask == null) {{
                    System.err.println("[SpawnChildren] No mapper found for: " + childName);
                    continue;
                }}
                ModelActions.spawnWithSpan(childTask);
            }}
        }} finally {{
            ct.setContextClassLoader(prev);
        }}
    }}
    
    public final class InputMapper implements InputType<Map<String, SerializedValue>> {{
        @Override
        public List<String> getRequiredParameters() {{
            return List.of();
        }}
        
        @Override
        public List<Parameter> getParameters() {{
            return List.of();
        }}
        
        @Override
        public Map<String, SerializedValue> getArguments(Map<String, SerializedValue> input) {{
            return input;
        }}
        
        @Override
        public Map<String, SerializedValue> instantiate(Map<String, SerializedValue> arguments) throws InstantiationException {{
            return arguments;
        }}
        
        @Override
        public List<ValidationNotice> getValidationFailures(Map<String, SerializedValue> input) {{
            return List.of();
        }}
    }}
    
    public static final class OutputMapper implements OutputType<Unit> {{
        private final ValueMapper<Unit> computedAttributesValueMapper = BasicValueMappers.$unit();
        
        @Override
        public ValueSchema getSchema() {{
            return this.computedAttributesValueMapper.getValueSchema();
        }}
        
        @Override
        public SerializedValue serialize(Unit returnValue) {{
            return this.computedAttributesValueMapper.serializeValue(returnValue);
        }}
    }}
}}
'''
    
    (pkg_dir / f"{class_name}.java").write_text(code)


def _compile_java_sources(src_dir: Path, build_dir: str):
    """Compile Java sources using javac."""
    
    # Find all Java files
    java_files = list(src_dir.rglob("*.java"))
    
    # Get merlin-sdk JAR from local Maven or GitHub Packages
    # For now, assume it's available via CLASSPATH or we'll download it
    
    classpath = _get_merlin_sdk_classpath()
    
    cmd = [
        "javac",
        "-d", str(Path(build_dir) / "classes"),
        "-cp", classpath,
    ] + [str(f) for f in java_files]
    
    subprocess.run(cmd, check=True)


def _package_jar(build_dir: str, output_jar: str, model_name: str, model_version: str, model_file: Path = None):
    """Package compiled classes into a JAR with SPI descriptor and dependencies."""
    
    classes_dir = Path(build_dir) / "classes"
    
    # Create META-INF/services directory
    services_dir = classes_dir / "META-INF" / "services"
    services_dir.mkdir(parents=True, exist_ok=True)
    
    # Write SPI descriptor
    (services_dir / "gov.nasa.jpl.aerie.merlin.protocol.model.MerlinPlugin").write_text(
        "gov.nasa.ammos.aerie.pymerlin.generated.GeneratedMerlinPlugin\n"
    )
    
    # Bundle Python model file if provided
    if model_file and model_file.exists():
        models_dir = classes_dir / "pymerlin_models"
        models_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(model_file, models_dir / model_file.name)
    
    # Extract dependency JARs into classes directory to create fat JAR
    # Use environment variables with same fallback logic as _get_merlin_sdk_classpath
    plandev_env = os.getenv("PLANDEV_PATH")
    if plandev_env:
        possible_plandev_locations = [Path(plandev_env).expanduser()]
    else:
        possible_plandev_locations = [
            Path.home() / "Desktop" / "pymerlin" / "plandev",
            Path.home() / "Desktop" / "plandev",
            Path(__file__).parent.parent.parent.parent / "plandev",
        ]
    
    plandev_base = None
    for location in possible_plandev_locations:
        if location.exists() and (location / "merlin-sdk").exists():
            plandev_base = location
            break
    
    dependency_jars = []
    if plandev_base:
        dependency_jars.extend([
            plandev_base / "merlin-sdk" / "build" / "libs" / "merlin-sdk.jar",
            plandev_base / "merlin-framework" / "build" / "libs" / "merlin-framework.jar",
            plandev_base / "merlin-driver" / "build" / "libs" / "merlin-driver.jar",
            plandev_base / "contrib" / "build" / "libs" / "contrib.jar",
        ])
    
    codegen_jar_path = os.getenv("PYMERLIN_CODEGEN_JAR", "/tmp/pymerlin-codegen.jar")
    dependency_jars.append(Path(codegen_jar_path).expanduser())
    
    # Find py4j JAR
    venv_path = os.getenv("PYMERLIN_VENV")
    if venv_path:
        possible_py4j_locations = [Path(venv_path).expanduser() / "share" / "py4j"]
    else:
        possible_py4j_locations = [
            Path(__file__).parent.parent.parent.parent / "venv" / "share" / "py4j",
            Path.home() / "Desktop" / "pymerlin" / "pymerlin" / "venv" / "share" / "py4j",
        ]
    
    for py4j_dir in possible_py4j_locations:
        if py4j_dir.exists():
            py4j_jars = list(py4j_dir.glob("py4j*.jar"))
            if py4j_jars:
                dependency_jars.append(py4j_jars[0])
                break
    
    for jar_path in dependency_jars:
        if jar_path.exists():
            # Extract JAR contents into classes directory
            subprocess.run([
                "jar", "xf", str(jar_path)
            ], cwd=str(classes_dir), check=True)
    
    # Create fat JAR
    subprocess.run([
        "jar", "cf", output_jar,
        "-C", str(classes_dir), "."
    ], check=True)


def _get_merlin_sdk_classpath():
    """Get classpath for merlin-sdk and dependencies."""
    # Try environment variable first, then fall back to common locations
    plandev_env = os.getenv("PLANDEV_PATH")
    if plandev_env:
        possible_plandev_locations = [Path(plandev_env).expanduser()]
    else:
        possible_plandev_locations = [
            Path.home() / "Desktop" / "pymerlin" / "plandev",
            Path.home() / "Desktop" / "plandev",
            Path(__file__).parent.parent.parent.parent / "plandev",
        ]
    
    plandev_base = None
    for location in possible_plandev_locations:
        if location.exists() and (location / "merlin-sdk").exists():
            plandev_base = location
            break
    
    if not plandev_base:
        raise FileNotFoundError(
            f"Cannot find plandev directory. Set PLANDEV_PATH in .env or searched: {[str(p) for p in possible_plandev_locations]}"
        )
    
    jars = []
    for jar_name in ["merlin-sdk.jar", "merlin-framework.jar", "merlin-driver.jar", "contrib.jar"]:
        jar_path = plandev_base / "merlin-sdk" / "build" / "libs" / jar_name
        if not jar_path.exists():
            jar_path = plandev_base / "merlin-framework" / "build" / "libs" / jar_name
        if not jar_path.exists():
            jar_path = plandev_base / "merlin-driver" / "build" / "libs" / jar_name
        if not jar_path.exists():
            jar_path = plandev_base / "contrib" / "build" / "libs" / jar_name
        if jar_path.exists():
            jars.append(str(jar_path))
    
    # Add pymerlin-codegen JAR
    codegen_jar_path = os.getenv("PYMERLIN_CODEGEN_JAR", "/tmp/pymerlin-codegen.jar")
    pymerlin_codegen_jar = Path(codegen_jar_path).expanduser()
    if pymerlin_codegen_jar.exists():
        jars.append(str(pymerlin_codegen_jar))
    
    # Add py4j JAR - check environment variable first
    venv_path = os.getenv("PYMERLIN_VENV")
    if venv_path:
        possible_py4j_locations = [Path(venv_path).expanduser() / "share" / "py4j"]
    else:
        possible_py4j_locations = [
            Path(__file__).parent.parent.parent.parent / "venv" / "share" / "py4j",
            Path.home() / "Desktop" / "pymerlin" / "pymerlin" / "venv" / "share" / "py4j",
        ]
    
    for py4j_dir in possible_py4j_locations:
        if py4j_dir.exists():
            py4j_jars = list(py4j_dir.glob("py4j*.jar"))
            if py4j_jars:
                jars.append(str(py4j_jars[0]))
                break
    
    if not jars:
        raise FileNotFoundError(
            f"Cannot find merlin JARs. Build plandev first in one of: {[str(p) for p in possible_plandev_locations]}"
        )
    
    return ":".join(jars)


def _to_java_class_name(python_name: str) -> str:
    """Convert Python snake_case to Java PascalCase."""
    return ''.join(word.capitalize() for word in python_name.split('_'))


def _python_type_to_java(python_type):
    """Map Python type annotations to Java types."""
    if python_type is int or python_type == 'int':
        return "Integer"
    if python_type is float or python_type == 'float':
        return "Double"
    if python_type is str or python_type == 'str':
        return "String"
    if python_type is bool or python_type == 'bool':
        return "Boolean"
    return "Object"
