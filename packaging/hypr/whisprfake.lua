-- whisprfake (local Wispr Flow) – installed to ~/.config/hypr/whisprfake.lua by packaging/install.sh
-- Loaded from ~/.config/hypr/hyprland.lua via: pcall(require, "hypr.whisprfake")

-- Ctrl+Super+Space starts hands-free dictation (like Wispr's Ctrl+Win+Space), so the background
-- switcher moves to Ctrl+Super+Shift+B.
hl.unbind("SUPER + CTRL + SPACE")
o.bind("SUPER + CTRL + SHIFT + B", "Background switcher", "omarchy-menu toggle background")

o.bind("SUPER + ALT + W", "whisprfake Hub", "whisprfake hub")
o.bind("SUPER + ALT + N", "whisprfake Scratchpad", "whisprfake scratchpad --toggle")

o.window("dev.whisprfake.Hub", { float = true })
o.window("dev.whisprfake.Hub", { center = true })
o.window("dev.whisprfake.Hub", { size = { 1100, 760 } })

o.window("dev.whisprfake.Scratchpad", { float = true })
o.window("dev.whisprfake.Scratchpad", { pin = true })
o.window("dev.whisprfake.Scratchpad", { size = { 560, 460 } })
o.window("dev.whisprfake.Scratchpad", { center = true })
