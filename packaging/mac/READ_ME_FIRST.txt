OpenFusion — test build for Apple Silicon Macs (M1 or newer, macOS 12+)
=========================================================================

INSTALL
1. Drag OpenFusion into the Applications folder.
2. Open it from Applications.

THE FIRST TIME, macOS WILL BLOCK IT
This is a friends-and-family build, not from the App Store, and it isn't
registered with Apple. macOS stops it the first time; you allow it once.

  macOS 15 (Sequoia) and newer:
    a. Double-click OpenFusion. A message says Apple could not verify it.
       Click "Done" (not "Move to Trash").
    b. Open System Settings > Privacy & Security, scroll down to Security,
       and click "Open Anyway" next to the OpenFusion line.
    c. Confirm with your password, then click "Open Anyway" again.

  macOS 14 (Sonoma) and older:
    Right-click (or Control-click) OpenFusion in Applications, choose Open,
    then click Open in the message.

  Or, in Terminal (any version):
    xattr -dr com.apple.quarantine /Applications/OpenFusion.app

After that it opens normally.

USING IT
- Start with New part, or open an example (it opens as a copy in
  Documents/OpenFusion, so you can't break the original).
- Click a face to build on it, an edge to fillet it. Nothing runs until OK.
- Press ? for the keyboard shortcuts. Control + letter are the tools
  (⌃B box, ⌃C cylinder, ⌃E push/pull, ⌃F fillet, ⌃M move, ⌃P pattern),
  Option + number are the views (⌥1 front ... ⌥0 home).
- Every OK saves the part (.ofs file) right away; ⌘Z undoes.
- Export (⌃⇧E, or right-click a body) writes 3MF / STL for your slicer.
- Double-click any .ofs file to open it in OpenFusion.

IF SOMETHING GOES WRONG
Send the part file (.ofs) and this log:
  ~/Library/Logs/OpenFusion.log
(In Finder: Go > Go to Folder..., paste the path.)
