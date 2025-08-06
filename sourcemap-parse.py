# Accept URL from user pointing to a sourcemap file and download it temporary
import argparse
import requests
import tempfile
import os
import json
import pathlib
import shutil
from urllib.parse import urlparse


def check_and_clean_output_directory(output_dir):
    """Check if output directory is empty, ask user for confirmation if not"""
    output_path = pathlib.Path(output_dir)

    # Check if directory exists and has contents
    if output_path.exists() and any(output_path.iterdir()):
        print(f"\nWarning: The output directory '{output_dir}' is not empty.")
        response = (
            input("Do you want to continue and delete existing contents? (y/N): ")
            .strip()
            .lower()
        )

        if response in ["y", "yes"]:
            print(f"Cleaning directory '{output_dir}'...")
            # Remove all contents but keep the directory
            for item in output_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            print("Directory cleaned successfully.")
            return True
        else:
            print("Operation cancelled by user.")
            return False

    return True


def download_sourcemap(url):
    """Download sourcemap from URL and return as JSON"""
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        response = requests.get(url)
        response.raise_for_status()
        tmp_file.write(response.content)
        temp_filename = tmp_file.name

    # Load the file as JSON
    with open(temp_filename, "r", encoding="utf-8") as f:
        sourcemap_json = json.load(f)

    # Delete the temporary file
    os.remove(temp_filename)
    return sourcemap_json


def extract_source_files(sourcemap_json, output_dir="extracted_sources"):
    """Extract all source files from sourcemap into a folder structure"""

    # Create output directory
    output_path = pathlib.Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Get sources and sourcesContent from sourcemap
    sources = sourcemap_json.get("sources", [])
    sources_content = sourcemap_json.get("sourcesContent", [])

    if not sources:
        print("No sources found in sourcemap")
        return

    if not sources_content:
        print("No source content found in sourcemap")
        return

    # Ensure we have matching arrays
    if len(sources) != len(sources_content):
        print(
            f"Warning: Mismatch between sources ({len(sources)}) and sourcesContent ({len(sources_content)})"
        )
        return

    print(f"Found {len(sources)} source files to extract")

    extracted_files = []

    for i, (source_path, source_content) in enumerate(zip(sources, sources_content)):
        if source_content is None:
            print(f"Skipping {source_path} - no content available")
            continue

        # Clean up the source path
        # Remove webpack:// or other prefixes
        clean_path = source_path
        if clean_path.startswith("webpack:///"):
            clean_path = clean_path[11:]  # Remove "webpack:///"
        elif clean_path.startswith("webpack://"):
            clean_path = clean_path[10:]  # Remove "webpack://"

        # Handle absolute paths that might start with /
        if clean_path.startswith("/"):
            clean_path = clean_path[1:]

        # Create the full file path
        file_path = output_path / clean_path

        # Ensure the directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the source content to file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(source_content)
            extracted_files.append(str(file_path))
            print(f"Extracted: {file_path}")
        except Exception as e:
            print(f"Error writing {file_path}: {e}")

    print(
        f"\nExtraction complete! {len(extracted_files)} files extracted to '{output_dir}' directory"
    )
    return extracted_files


def analyze_sourcemap(sourcemap_json):
    """Analyze and display information about the sourcemap"""
    print("=== Sourcemap Analysis ===")

    # Basic info
    version = sourcemap_json.get("version", "unknown")
    file = sourcemap_json.get("file", "unknown")
    source_root = sourcemap_json.get("sourceRoot", "")

    print(f"Version: {version}")
    print(f"File: {file}")
    print(f"Source Root: {source_root}")

    # Sources info
    sources = sourcemap_json.get("sources", [])
    sources_content = sourcemap_json.get("sourcesContent", [])

    print(f"Number of sources: {len(sources)}")
    print(f"Number of source contents: {len(sources_content)}")

    # Show first few sources
    if sources:
        print("\nFirst 5 source files:")
        for i, source in enumerate(sources[:5]):
            has_content = (
                "✓" if i < len(sources_content) and sources_content[i] else "✗"
            )
            print(f"  {has_content} {source}")

        if len(sources) > 5:
            print(f"  ... and {len(sources) - 5} more")

    # Names info
    names = sourcemap_json.get("names", [])
    print(f"Number of names: {len(names)}")

    # Mappings info (just length, not content)
    mappings = sourcemap_json.get("mappings", "")
    print(f"Mappings length: {len(mappings)} characters")

    print("=" * 30)


def main():
    parser = argparse.ArgumentParser(
        description="Download a sourcemap file from a URL and extract all source code."
    )
    parser.add_argument("url", help="URL to the sourcemap file")
    parser.add_argument(
        "--output",
        "-o",
        default="extracted_sources",
        help="Output directory for extracted sources (default: extracted_sources)",
    )
    parser.add_argument(
        "--analyze",
        "-a",
        action="store_true",
        help="Analyze the sourcemap structure without extracting",
    )

    args = parser.parse_args()

    try:
        print(f"Downloading sourcemap from: {args.url}")
        sourcemap_json = download_sourcemap(args.url)

        # Analyze the sourcemap
        analyze_sourcemap(sourcemap_json)

        if not args.analyze:
            # Check and clean output directory if needed
            if not check_and_clean_output_directory(args.output):
                return

            # Extract the source files
            extract_source_files(sourcemap_json, args.output)

    except requests.exceptions.RequestException as e:
        print(f"Error downloading sourcemap: {e}")
    except json.JSONDecodeError as e:
        print(f"Error parsing sourcemap JSON: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
