"""Runtime hook: force matplotlib to use TkAgg backend (before any imports)"""
import os

os.environ['MPLBACKEND'] = 'TkAgg'
