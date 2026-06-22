package gov.nasa.ammos.aerie.merlin.python.graal;

import gov.nasa.jpl.aerie.merlin.protocol.model.MerlinPlugin;
import gov.nasa.jpl.aerie.merlin.protocol.model.ModelType;
import org.graalvm.polyglot.*;
import org.graalvm.python.embedding.GraalPyResources;

public final class PyMerlinPlugin implements MerlinPlugin {

    // Context is held open for the plugin's lifetime so GraalPy threads
    // spawned during simulation remain valid. It is closed by Aerie's worker
    // process exit (no explicit close needed for the model lifecycle).
    private final Context context;
    private final ModelType<?, ?> modelType;

    public PyMerlinPlugin() {
        var modelModule = ResourceConfig.get("pymerlin.model.module");
        var modelClass  = ResourceConfig.get("pymerlin.model.class");

        context = GraalPyResources.contextBuilder()
                .allowHostAccess(HostAccess.ALL)
                .allowCreateThread(true)
                .allowAllAccess(true)
                .build();

        // Bootstrap: import the user model, wrap in pymerlin's ModelType,
        // then inject a GraalGateway so gateway.jvm.* resolves via java.type().
        Value bootstrap = context.eval("python", """
                def _build(module_name, class_name):
                    import importlib
                    from pymerlin._internal._model_type import ModelType
                    from pymerlin._internal._graal_gateway import GraalGateway
                    mod = importlib.import_module(module_name)
                    mt = ModelType(getattr(mod, class_name))
                    mt.set_gateway(GraalGateway())
                    return mt
                _build
                """);

        Value pyModelType = bootstrap.execute(modelModule, modelClass);
        modelType = pyModelType.as(ModelType.class);
    }

    @Override
    public ModelType<?, ?> getModelType() {
        return modelType;
    }
}