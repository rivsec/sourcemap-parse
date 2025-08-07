# Accept URL from user pointing to a sourcemap file and download it temporary
import argparse
import requests
import tempfile
import os
import json
import pathlib
import shutil
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import asyncio
import aiohttp


def get_script_tags(url, proxy=None):
    """
    Get all script tags from the <head> element of a webpage.

    Args:
        url (str): The URL of the webpage to check
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        list: List of script tag dictionaries with 'src' and 'tag' keys
    """
    try:
        # Make request to the webpage
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 River Security"
        }

        # Configure proxy if provided
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        response = requests.get(url, headers=headers, timeout=10, proxies=proxies)
        response.raise_for_status()

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")

        # Find the head element
        head = soup.find("head")
        if not head:
            logging.info("No <head> element found")

        # After checking <head>, also check the rest of the document for <script> tags
        # that are not in <head> (e.g., in <body> or elsewhere).
        # We'll collect all <script> tags from the document, then filter out those already in head.
        all_script_tags = soup.find_all("script")

        scripts = []
        for script in all_script_tags:
            src = script.get("src")
            if src:
                # Convert relative URLs to absolute URLs
                if not src.startswith(("http://", "https://")):
                    src = urljoin(url, src)
                parsed_url = urlparse(url)
                parsed_src = urlparse(src)
                if parsed_url.netloc == parsed_src.netloc:
                    scripts.append({"src": src, "tag": str(script)})
                else:
                    logging.info(f"Skipping script from different domain: {src}")

        return scripts

    except requests.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return []
    except Exception as e:
        logging.error(f"Error parsing HTML: {e}")
        return []


def setup_logging():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


async def check_for_sourcemaps(script_urls, proxy=None):
    """
    Async version of check_for_sourcemaps that processes URLs concurrently.
    """
    results = {}

    # If proxy is provided, we'll use synchronous requests for better proxy support
    if proxy:
        logging.info(
            "Proxy detected, using synchronous requests for better compatibility"
        )
        return check_for_sourcemaps_sync(script_urls, proxy)

    async with aiohttp.ClientSession() as session:
        # Create tasks for all script URLs
        tasks = [
            check_single_script_async(session, script_url, proxy)
            for script_url in script_urls
        ]

        # Execute all tasks concurrently
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for script_url, result in zip(script_urls, completed_tasks):
            if isinstance(result, Exception):
                logging.error(f"Error processing {script_url}: {result}")
                results[script_url] = []
            else:
                results[script_url] = result

    return results


def check_for_sourcemaps_sync(script_urls, proxy=None):
    """
    Synchronous fallback for proxy support when aiohttp proxy handling fails.
    """
    results = {}

    # Configure proxy if provided
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    for script_url in script_urls:
        logging.info(f"Checking: {script_url}")
        sourcemaps = []

        # Method 1: Check for source map comment in script content
        try:
            response = requests.get(script_url, timeout=10, proxies=proxies)
            if response.status_code == 200:
                # Look for source map comment
                sourcemap_comment = find_sourcemap_comment(response.text)
                if sourcemap_comment:
                    sourcemap_url = urljoin(script_url, sourcemap_comment)
                    if check_url_exists(sourcemap_url, proxy):
                        sourcemaps.append(
                            {
                                "url": sourcemap_url,
                                "method": "comment",
                                "comment": sourcemap_comment,
                            }
                        )
        except Exception as e:
            logging.error(f"  Error checking script content: {e}")

        # Method 2: Try common source map URL patterns
        common_sourcemaps = check_common_sourcemap_patterns(script_url, proxy)
        for sourcemap in common_sourcemaps:
            if sourcemap not in [s["url"] for s in sourcemaps]:  # Avoid duplicates
                sourcemaps.append({"url": sourcemap, "method": "pattern"})

        results[script_url] = sourcemaps

    return results


async def check_single_script_async(session, script_url, proxy=None):
    """
    Async version of checking a single script for sourcemaps.
    """
    logging.info(f"Checking: {script_url}")
    sourcemaps = []

    # Method 1: Check for source map comment in script content
    try:
        # Configure proxy for this request if provided
        request_kwargs = {"timeout": aiohttp.ClientTimeout(total=10)}
        if proxy:
            request_kwargs["proxy"] = proxy

        async with session.get(script_url, **request_kwargs) as response:
            if response.status == 200:
                content = await response.text()
                sourcemap_comment = find_sourcemap_comment(content)
                if sourcemap_comment:
                    sourcemap_url = urljoin(script_url, sourcemap_comment)
                    if await check_url_exists_async(session, sourcemap_url, proxy):
                        sourcemaps.append(
                            {
                                "url": sourcemap_url,
                                "method": "comment",
                                "comment": sourcemap_comment,
                            }
                        )
    except Exception as e:
        logging.error(f"  Error checking script content: {e}")

    # Method 2: Try common source map URL patterns
    common_sourcemaps = await check_common_sourcemap_patterns_async(
        session, script_url, proxy
    )
    for sourcemap in common_sourcemaps:
        if sourcemap not in [s["url"] for s in sourcemaps]:
            sourcemaps.append({"url": sourcemap, "method": "pattern"})

    return sourcemaps


async def check_common_sourcemap_patterns_async(session, script_url, proxy=None):
    """
    Async version of checking common sourcemap patterns.
    """
    parsed_url = urlparse(script_url)
    path = parsed_url.path
    base_path = path.rsplit(".", 1)[0] if "." in path else path

    patterns = [
        f"{base_path}.map",
        f"{base_path}.js.map",
        f"{base_path}.css.map",
        f"{path}.map",
        f"{path}.js.map",
        f"{path}.css.map",
    ]

    accessible_sourcemaps = []
    for pattern in patterns:
        sourcemap_url = f"{parsed_url.scheme}://{parsed_url.netloc}{pattern}"
        if await check_url_exists_async(session, sourcemap_url, proxy):
            accessible_sourcemaps.append(sourcemap_url)

    return accessible_sourcemaps


async def check_url_exists_async(session, url, proxy=None):
    """
    Async version of checking if a URL exists and is a valid sourcemap.
    """
    try:
        # Configure proxy for this request if provided
        request_kwargs = {"timeout": aiohttp.ClientTimeout(total=5)}
        if proxy:
            request_kwargs["proxy"] = proxy

        async with session.get(url, **request_kwargs) as response:
            if response.status == 200:
                data = await response.json()
                return (
                    isinstance(data, dict)
                    and "version" in data
                    and "file" in data
                    and "mappings" in data
                )
        return False
    except Exception as e:
        logging.info(f"Not a source map: {url}: {e}")
        return False


def find_sourcemap_comment(script_content):
    """
    Find source map comment in script content.

    Args:
        script_content (str): The script content to search

    Returns:
        str or None: Source map URL from comment if found
    """
    patterns = [
        r"//# sourceMappingURL=([^\s]+)",
        r"//@ sourceMappingURL=([^\s]+)",
        r"/\*# sourceMappingURL=([^\s]+) \*/",
        r"/\*@ sourceMappingURL=([^\s]+) @\*/",
    ]

    for pattern in patterns:
        match = re.search(pattern, script_content)
        if match:
            return match.group(1)

    return None


def check_common_sourcemap_patterns(script_url, proxy=None):
    """
    Check common source map URL patterns for a script.

    Args:
        script_url (str): The script URL
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        list: List of accessible source map URLs
    """
    parsed_url = urlparse(script_url)
    path = parsed_url.path
    base_path = path.rsplit(".", 1)[0] if "." in path else path

    # Common source map patterns
    patterns = [
        f"{base_path}.map",
        f"{base_path}.js.map",
        f"{base_path}.css.map",
        f"{path}.map",
        f"{path}.js.map",
        f"{path}.css.map",
    ]

    accessible_sourcemaps = []
    for pattern in patterns:
        sourcemap_url = f"{parsed_url.scheme}://{parsed_url.netloc}{pattern}"
        if check_url_exists(sourcemap_url, proxy):
            accessible_sourcemaps.append(sourcemap_url)

    return accessible_sourcemaps


def check_url_exists(url, proxy=None):
    """
    Check if a URL exists by making a GET request and validating it's a source map.

    Args:
        url (str): URL to check
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        bool: True if URL exists and is a valid source map
    """
    try:
        # Configure proxy if provided
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        response = requests.get(url, timeout=5, proxies=proxies)
        data = response.json()
        if not (
            isinstance(data, dict)
            and "version" in data
            and "file" in data
            and "mappings" in data
        ):
            return False
        return True
    except Exception as e:
        logging.info(f"Not a source map: {url}: {e}")
        return False


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

        # Sanitize the path to prevent directory traversal
        # Split the path and filter out any '..' or '.' components
        path_parts = clean_path.split("/")
        sanitized_parts = []
        for part in path_parts:
            if part not in ["..", "."] and part.strip():
                sanitized_parts.append(part)

        # Reconstruct the sanitized path
        clean_path = "/".join(sanitized_parts)

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
    # Setup logging first
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Download a sourcemap file from a URL and extract all source code."
    )
    parser.add_argument("url", help="URL to the sourcemap file")

    parser.add_argument(
        "--proxy",
        "-p",
        help="Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')",
    )
    # Require a output folder directory where files will be extracted to . Only required if --extract_sources is used
    # Argument group for extract_sources
    extract_sources_group = parser.add_argument_group("Extract Sources")
    extract_sources_group.add_argument(
        "--extract_sources",
        "-e",
        action="store_true",
        help="Extract the source files from the sourcemap",
    )
    extract_sources_group.add_argument(
        "--output_dir",
        "-o",
        help="Output directory where files will be extracted to",
    )

    args = parser.parse_args()
    if args.extract_sources:
        if not args.output_dir:
            logging.info("Output directory is required when using --extract_sources")
            return

    # If output_dir , check if it exists and is empty. If not, ask user for confirmation to delete it.
    if args.output_dir:
        if not os.path.exists(args.output_dir):
            os.makedirs(args.output_dir)
        else:
            if os.listdir(args.output_dir):
                logging.info("Output directory is not empty")
                response = input(
                    "Do you want to continue and delete existing contents? (y/N): "
                )
                if response in ["y", "yes"]:
                    shutil.rmtree(args.output_dir)
                    os.makedirs(args.output_dir)
                else:
                    logging.info("Operation cancelled by user.")
                    return

    url = args.url.strip()
    proxy = args.proxy
    output_dir = args.output_dir
    if not url:
        logging.info("No URL provided")
        return

    if proxy:
        logging.info(f"Using proxy: {proxy}")

    logging.info(f"Checking {url} for script tags...")
    # Get script tags from head
    scripts = get_script_tags(url, proxy)

    if not scripts:
        logging.info("No script tags with src attributes from this domain found")
        return

    logging.info(f"Found {len(scripts)} script tag(s):")
    for i, script in enumerate(scripts, 1):
        logging.info(f"{script['src']}")

    logging.info("Checking for source map files...")

    # Check for source maps using async
    script_urls = [script["src"] for script in scripts]
    sourcemap_results = asyncio.run(check_for_sourcemaps(script_urls, proxy))

    # Summary
    logging.info("Summary:")
    total_sourcemaps = sum(len(sourcemaps) for sourcemaps in sourcemap_results.values())
    logging.info(f"Scripts checked: {len(scripts)}")
    logging.info(f"Source maps found: {total_sourcemaps}")

    if total_sourcemaps > 0:
        logging.info("All source maps found:")
        for script_url, sourcemaps in sourcemap_results.items():
            if sourcemaps:
                for sourcemap in sourcemaps:
                    print(f"{sourcemap['url']}")
                    if args.extract_sources:
                        try:
                            output_dir = (
                                f"{args.output_dir}/{script_url.split('/')[-1]}"
                            )
                            logging.info(
                                f"Downloading sourcemap from: {sourcemap['url']}"
                            )
                            sourcemap_json = download_sourcemap(sourcemap["url"])

                            # Analyze the sourcemap
                            analyze_sourcemap(sourcemap_json)

                            # Check and clean output directory if needed
                            if not check_and_clean_output_directory(output_dir):
                                continue

                            # Extract the source files
                            extract_source_files(sourcemap_json, output_dir)

                        except requests.exceptions.RequestException as e:
                            logging.info(f"Error downloading sourcemap: {e}")
                        except json.JSONDecodeError as e:
                            logging.info(f"Error parsing sourcemap JSON: {e}")
                        except Exception as e:
                            logging.error(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
