#!/usr/bin/env python3
"""
Release All Jupyter Instances

This script pauses and deletes all non-stopped Jupyter instances.
Useful for resource cleanup and batch operations.

The API key can be provided via:
1. PYROMIND_API_KEY environment variable (recommended)
2. api_key parameter when initializing the client

If neither is provided, the client will raise a ValueError.
"""

import time
from pyromind_sdk import PyroMindAPIClient, PyroMindAPIError


def release_all_instances(
    pause_wait_seconds: int = 10,
    skip_confirmation: bool = False
) -> dict:
    """
    Pause and delete all non-stopped Jupyter instances.

    Args:
        pause_wait_seconds: Seconds to wait after pausing before deleting (default: 10)
                           Note: This is a workaround for a known issue where instances
                           cannot be deleted immediately after pausing.
        skip_confirmation: Skip confirmation prompt (default: False)

    Returns:
        Dictionary with operation results:
        - total: Total number of instances found
        - paused: Number of instances paused
        - deleted: Number of instances deleted
        - errors: List of error messages
    """
    client = PyroMindAPIClient()

    try:
        # List all instances
        print("Fetching all Jupyter instances...")
        instances = client.jupyter.list()
        print(f"Found {len(instances)} instance(s)")

        if not instances:
            print("No instances to release.")
            return {"total": 0, "paused": 0, "deleted": 0, "errors": []}

        # Filter instances that are not stopped
        running_instances = [i for i in instances if i.status != 'stopped']

        if not running_instances:
            print("All instances are already stopped.")
            # Ask if user wants to delete stopped instances
            if not skip_confirmation:
                response = input("Delete all stopped instances? (y/N): ")
                if response.lower() != 'y':
                    return {
                        "total": len(instances),
                        "paused": 0,
                        "deleted": 0,
                        "errors": []
                    }
                instances_to_delete = instances
            else:
                instances_to_delete = instances
        else:
            instances_to_delete = instances

            # Show instances to be released
            print("\nInstances to be released:")
            for instance in running_instances:
                print(f"  - {instance.name} (ID: {instance.id}, Status: {instance.status})")

            # Ask for confirmation
            if not skip_confirmation:
                response = input(f"\nRelease {len(running_instances)} running instance(s)? (y/N): ")
                if response.lower() != 'y':
                    print("Operation cancelled.")
                    return {"total": len(instances), "paused": 0, "deleted": 0, "errors": []}

        results = {
            "total": len(instances),
            "paused": 0,
            "deleted": 0,
            "errors": []
        }

        # Pause running instances
        if running_instances:
            print(f"\nPausing {len(running_instances)} running instance(s)...")
            for instance in running_instances:
                try:
                    print(f"  Pausing {instance.name} (ID: {instance.id})...")
                    client.jupyter.pause(instance.id)
                    results["paused"] += 1
                    print(f"    ✓ Paused")
                except PyroMindAPIError as e:
                    error_msg = f"Failed to pause {instance.name}: {e.message}"
                    print(f"    ✗ {error_msg}")
                    results["errors"].append(error_msg)

            # Wait for instances to fully pause
            # TODO: Remove this wait once the API allows immediate deletion after pausing
            if pause_wait_seconds > 0:
                print(f"\nWaiting {pause_wait_seconds} seconds for instances to fully pause...")
                print("Note: This is a workaround for a known API limitation.")
                time.sleep(pause_wait_seconds)

        # Delete all instances
        print(f"\nDeleting {len(instances_to_delete)} instance(s)...")
        for instance in instances_to_delete:
            try:
                print(f"  Deleting {instance.name} (ID: {instance.id})...")
                client.jupyter.delete(instance.id)
                results["deleted"] += 1
                print(f"    ✓ Deleted")
            except PyroMindAPIError as e:
                error_msg = f"Failed to delete {instance.name}: {e.message}"
                print(f"    ✗ {error_msg}")
                results["errors"].append(error_msg)

        # Print summary
        print("\n" + "=" * 60)
        print("Summary:")
        print(f"  Total instances: {results['total']}")
        print(f"  Paused: {results['paused']}")
        print(f"  Deleted: {results['deleted']}")
        if results['errors']:
            print(f"  Errors: {len(results['errors'])}")
        print("=" * 60)

        return results

    except PyroMindAPIError as e:
        print(f"✗ API Error: {e.message}")
        if e.status_code:
            print(f"  Status Code: {e.status_code}")
        if e.response:
            print(f"  Response: {e.response}")
        return {
            "total": 0,
            "paused": 0,
            "deleted": 0,
            "errors": [str(e)]
        }
    finally:
        client.close()
        print("\nClient closed.")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Release all Jupyter instances",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (with confirmation)
  python release_all_instance.py

  # Skip confirmation
  python release_all_instance.py --yes

  # Custom wait time
  python release_all_instance.py --wait 5

  # Delete without waiting
  python release_all_instance.py --wait 0 --yes
        """
    )

    parser.add_argument(
        '--wait',
        type=int,
        default=10,
        help='Seconds to wait after pausing before deleting (default: 10)'
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Skip confirmation prompt'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Release All Jupyter Instances")
    print("=" * 60)

    release_all_instances(
        pause_wait_seconds=args.wait,
        skip_confirmation=args.yes
    )


if __name__ == "__main__":
    main()
