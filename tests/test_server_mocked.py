"""Compatibility entry point for the new TLS loopback regression suite."""
import unittest
from test_tls_integration import SecurityTests

if __name__ == "__main__":
    unittest.main(verbosity=2)
