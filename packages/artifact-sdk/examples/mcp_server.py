"""Local blocking file echo; use the existing remote-job example for durable jobs."""
import argparse
import os
import shutil
import tempfile
from pathlib import Path

from fastmcp import FastMCP
from tcode_artifacts import ArtifactIO

mcp = FastMCP('Artifact SDK file echo')


@mcp.tool
def echo_file(input_file: dict) -> dict:
    """Download one ArtifactRef and return the exact bytes in an output BundleRef."""
    if input_file.get('kind') != 'artifact':
        raise ValueError('echo_file requires an ArtifactRef')
    io = ArtifactIO()
    with tempfile.TemporaryDirectory(prefix='artifact-echo-') as directory:
        # macOS tempfile may use /var -> /private/var; canonicalize this
        # application-created private root, not untrusted input references.
        root = Path(directory).resolve()
        source = io.materialize(input_file, root / 'input.dat')
        output = root / 'output'
        output.mkdir()
        shutil.copyfile(source, output / 'echo.dat')
        return {'status': 'completed', 'summary': 'Input bytes echoed without scientific computation',
                'output_bundle': io.upload_directory(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Local file echo MCP')
    parser.add_argument('--port', type=int, default=int(os.environ.get('EXAMPLE_PORT', '10240')))
    parser.add_argument('--with-artifact', action='store_true', help='Also serve files unless an external Artifact is configured')
    parser.add_argument('--data-dir', type=Path, default=Path('./artifact-data'))
    parser.add_argument('--service-id')
    args = parser.parse_args()
    # Loopback example only; use independent MCP authentication and TLS remotely.
    if args.with_artifact:
        from tcode_artifacts.server import run_with_artifact

        try:
            run_with_artifact(mcp, port=args.port, data_dir=args.data_dir, service_id=args.service_id)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        mcp.run(transport='streamable-http', host='127.0.0.1', port=args.port)
