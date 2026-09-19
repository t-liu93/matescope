#!/usr/bin/env sh

# The T34 WebKit check can use a caller-supplied user-state library root. This
# mirrors the bundled WPE launcher without modifying the host or browser cache.
: "${MATESCOPE_M1_T34_WEBKIT_DIR:?Set the Playwright WebKit bundle directory}"
: "${MATESCOPE_M1_T34_WEBKIT_LIB_DIR:?Set the temporary user-state library directory}"
webkit_dir="$MATESCOPE_M1_T34_WEBKIT_DIR/minibrowser-wpe"
export WEBKIT_EXEC_PATH="$webkit_dir/bin"
export WEBKIT_INJECTED_BUNDLE_PATH="$webkit_dir/lib"
export WEBKIT_INSPECTOR_RESOURCES_PATH="$webkit_dir/share"
export LD_LIBRARY_PATH="$webkit_dir/lib:$MATESCOPE_M1_T34_WEBKIT_LIB_DIR:$webkit_dir/sys/lib"
exec "$webkit_dir/bin/MiniBrowser" "$@"
