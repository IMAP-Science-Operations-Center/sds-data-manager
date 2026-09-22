"""Shared IP allowlists (CIDR blocks) for security-group ingress rules."""

ALLOWED_CIDRS: list[str] = [
    "128.138.131.0/24",  # LASP
    "128.112.0.0/16",  # Princeton
    "140.180.0.0/16",  # Princeton
    "204.153.48.0/22",  # Princeton
    "12.161.8.0/24",  # Princeton
    "12.161.10.0/24",  # Princeton
    "12.161.14.0/24",  # Princeton
    "66.180.176.0/24",  # Princeton
    "66.180.177.0/24",  # Princeton
    "66.180.184.0/22",  # Princeton
    "132.177.251.17/32",  # UNH
]
