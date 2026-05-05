"""field — retrofittable OS-layer dispatcher for accumulated filesystems.

The companion to bubble: where bubble does demand-paged Python imports
out of a content-addressed package vault, field does demand-paged binary
dispatch out of a snapshot of a real prior filesystem. Stage 0 is the
static-binary probe; later stages add bwrap, fault loops, multi-candidate
UX, closure observation, and projection-via-bundle.
"""
