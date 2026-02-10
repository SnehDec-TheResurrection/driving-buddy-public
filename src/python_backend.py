import sys

# read CSV row from stdin
csv_row = sys.stdin.read().strip()

with open("output.csv", "a") as f:
    f.write(csv_row + "\n")
