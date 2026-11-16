from tcode_agent.plugin_sdk import PluginMetadata


class Plugin:
    info = PluginMetadata(name="json-async-sum", version="1.0.0", description="JSON async sum")

    async def start(self, ctx, config):
        pass  # Only the manifest registers capabilities; no network calls here.
