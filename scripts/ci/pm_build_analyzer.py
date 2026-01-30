
#!/usr/bin/env python3
import os
import sys
import csv

def scan_build_directories(root_path):
    results = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        if "build.log" in filenames:
            has_partitions = "partitions.yml" in filenames
            results.append((dirpath, has_partitions))

    return results


def write_csv(results, output_file="build_report.csv"):
    with open(output_file, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["path", "partitions_present"])

        for path, partitions_present in results:
            writer.writerow([path, str(partitions_present).lower()])


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scan_builds.py <build_root_path>")
        sys.exit(1)

    root_path = sys.argv[1]

    if not os.path.isdir(root_path):
        print(f"Error: '{root_path}' is not a valid directory.")
        sys.exit(1)

    results = scan_build_directories(root_path)
    write_csv(results)

    print(f"Scan complete. Report written to build_report.csv")
    print(f"Total build directories found: {len(results)}")


if __name__ == "__main__":
    main()
