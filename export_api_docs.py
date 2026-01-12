#!/usr/bin/env python3
"""
Export FastAPI OpenAPI spec as Markdown documentation

This script generates API documentation in Markdown format from the
FastAPI OpenAPI schema and saves it to docs/API.md
"""

import json
from pathlib import Path
from typing import Any, Dict, List


def format_parameter(param: Dict[str, Any]) -> str:
    """Format a parameter for markdown display"""
    name = param.get("name", "")
    location = param.get("in", "")
    required = " *(required)*" if param.get("required", False) else ""
    param_type = param.get("schema", {}).get("type", "string")
    description = param.get("description", "")

    parts = [f"**{name}**"]
    if description:
        parts.append(f"- {description}")
    parts.append(f"  - Type: `{param_type}`")
    parts.append(f"  - Location: {location}{required}")

    # Add pattern if it exists
    if "schema" in param and "pattern" in param["schema"]:
        parts.append(f"  - Pattern: `{param['schema']['pattern']}`")

    return "\n".join(parts)


def format_request_body(request_body: Dict[str, Any]) -> str:
    """Format request body for markdown display"""
    if not request_body:
        return ""

    content = request_body.get("content", {})
    description = request_body.get("description", "")

    lines = ["### Request Body\n"]
    if description:
        lines.append(f"{description}\n")

    for content_type, schema_info in content.items():
        lines.append(f"**Content-Type:** `{content_type}`\n")

        schema = schema_info.get("schema", {})
        if "type" in schema:
            lines.append(f"**Type:** `{schema['type']}`\n")

    return "\n".join(lines)


def format_response(status_code: str, response: Dict[str, Any]) -> str:
    """Format a response for markdown display"""
    description = response.get("description", "")
    content = response.get("content", {})

    lines = [f"#### {status_code}"]
    if description:
        lines.append(f"\n{description}\n")

    for content_type, schema_info in content.items():
        lines.append(f"\n**Content-Type:** `{content_type}`")

        # Show example if available
        if "example" in schema_info:
            lines.append(f"\n```json\n{json.dumps(schema_info['example'], indent=2)}\n```")

    return "\n".join(lines)


def generate_markdown(openapi_spec: Dict[str, Any]) -> str:
    """Convert OpenAPI spec to Markdown"""

    lines = []

    # Title and Description
    info = openapi_spec.get("info", {})
    lines.append(f"# {info.get('title', 'API Documentation')}\n")
    lines.append(f"**Version:** {info.get('version', '1.0.0')}\n")

    if "description" in info:
        lines.append(f"{info['description']}\n")

    # Contact and License
    if "contact" in info:
        contact = info["contact"]
        lines.append("## Contact\n")
        if "name" in contact:
            lines.append(f"- **Name:** {contact['name']}")
        if "url" in contact:
            lines.append(f"- **URL:** {contact['url']}")
        if "email" in contact:
            lines.append(f"- **Email:** {contact['email']}")
        lines.append("")

    if "license" in info:
        license_info = info["license"]
        lines.append(f"**License:** {license_info.get('name', 'N/A')}\n")

    lines.append("---\n")

    # Servers
    if "servers" in openapi_spec:
        lines.append("## Servers\n")
        for server in openapi_spec["servers"]:
            url = server.get("url", "")
            description = server.get("description", "")
            lines.append(f"- **{url}**")
            if description:
                lines.append(f"  - {description}")
        lines.append("")

    # Table of Contents
    lines.append("## Table of Contents\n")

    # Group paths by tags
    paths = openapi_spec.get("paths", {})
    tags_dict: Dict[str, List[tuple]] = {}

    for path, methods in paths.items():
        for method, operation in methods.items():
            if method in ["get", "post", "put", "delete", "patch", "options", "head"]:
                tags = operation.get("tags", ["Default"])
                summary = operation.get("summary", path)

                for tag in tags:
                    if tag not in tags_dict:
                        tags_dict[tag] = []
                    tags_dict[tag].append((method.upper(), path, summary, operation))

    # Generate TOC
    for tag in sorted(tags_dict.keys()):
        lines.append(f"- [{tag}](#{tag.lower().replace(' ', '-')})")
    lines.append("")

    # Generate endpoint documentation by tag
    for tag in sorted(tags_dict.keys()):
        lines.append(f"## {tag}\n")

        for method, path, summary, operation in tags_dict[tag]:
            # Endpoint header
            lines.append(f"### {method} `{path}`\n")
            lines.append(f"**{summary}**\n")

            # Description
            if "description" in operation:
                lines.append(f"{operation['description']}\n")

            # Parameters
            if "parameters" in operation and operation["parameters"]:
                lines.append("#### Parameters\n")
                for param in operation["parameters"]:
                    lines.append(format_parameter(param))
                    lines.append("")

            # Request Body
            if "requestBody" in operation:
                lines.append(format_request_body(operation["requestBody"]))

            # Responses
            if "responses" in operation:
                lines.append("#### Responses\n")
                for status_code, response in operation["responses"].items():
                    lines.append(format_response(status_code, response))
                    lines.append("")

            # Security
            if "security" in operation:
                lines.append("#### Security\n")
                for security_req in operation["security"]:
                    for scheme_name in security_req.keys():
                        lines.append(f"- **{scheme_name}**")
                lines.append("")

            lines.append("---\n")

    # Security Schemes
    if "components" in openapi_spec and "securitySchemes" in openapi_spec["components"]:
        lines.append("## Security Schemes\n")
        for scheme_name, scheme in openapi_spec["components"]["securitySchemes"].items():
            lines.append(f"### {scheme_name}\n")
            lines.append(f"**Type:** {scheme.get('type', 'N/A')}\n")
            if "description" in scheme:
                lines.append(f"{scheme['description']}\n")
            if scheme.get("type") == "oauth2":
                flows = scheme.get("flows", {})
                for flow_type, flow_data in flows.items():
                    lines.append(f"**Flow:** {flow_type}")
                    if "tokenUrl" in flow_data:
                        lines.append(f"- Token URL: `{flow_data['tokenUrl']}`")
                    if "scopes" in flow_data:
                        lines.append("- Scopes:")
                        for scope, desc in flow_data["scopes"].items():
                            lines.append(f"  - `{scope}`: {desc}")
            lines.append("")

    return "\n".join(lines)


def main():
    """Main function to export API docs"""
    print("Generating API documentation...")

    # Import the FastAPI app
    try:
        from main import app
    except ImportError as e:
        print(f"Error: Could not import FastAPI app: {e}")
        print("Make sure you're running this script from the nova-hub directory")
        return 1

    # Get OpenAPI spec
    openapi_spec = app.openapi()

    # Generate markdown
    markdown_content = generate_markdown(openapi_spec)

    # Save to docs folder
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    output_file = docs_dir / "API.md"
    output_file.write_text(markdown_content)

    print(f"✓ API documentation exported to: {output_file}")
    print(f"  - {len(openapi_spec.get('paths', {}))} endpoints documented")
    print(f"  - {len(markdown_content.splitlines())} lines generated")

    # Also save raw OpenAPI JSON for reference
    json_file = docs_dir / "openapi.json"
    json_file.write_text(json.dumps(openapi_spec, indent=2))
    print(f"✓ OpenAPI JSON exported to: {json_file}")

    return 0


if __name__ == "__main__":
    exit(main())
