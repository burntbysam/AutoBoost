"""Navigation layer for AutoBoost.

Boost is driven through two top-level windows:
  - HomeZone (WPF, rich automation_ids)  -- part list, open/save/close, BOOST
  - Design   (WinForms/DotNetBar)        -- ribbon + PropertyGrid (font chain)

The graphics canvas inside Design is an opaque render surface with no UIA
children, so canvas work (placing text, selecting it) is handled by vision +
simulated input, not this layer. See boost_uia.py.
"""
