def parse(line):
    parts = line.split(",")
    if len(parts) != 2:
        return None
    return parts
