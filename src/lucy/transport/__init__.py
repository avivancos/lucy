"""Lucy control-channel transport (ADR 0011).

The versioned control channel carries events and directives between the media
gateway and the Python cognition plane - never audio bytes (ADR 0004). This
package defines the wire schema (``schema``) and the deterministic in-process
gateway simulator (``dev_gateway``) used for no-mocks tests.
"""
