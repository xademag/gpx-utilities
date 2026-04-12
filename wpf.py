"""
wpf.py — IronPython wpf-module compatibility shim for pythonnet/CPython.

pythonnet's DependencyObject subclassing leaves Window/Page in a state where
SetValue fails during __init__. This shim avoids that by loading XAML with
XamlReader.Load() (which creates a fully-initialised WPF object) and storing
it as component._wpf.  Named children are also bound as Python attributes.
"""
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows.Markup import XamlReader
from System.IO import FileStream, FileMode
from System.Windows import LogicalTreeHelper


def LoadComponent(component, xaml_path):
    """Load *xaml_path*, store the WPF object as component._wpf, and bind
    every named child element as a Python attribute on *component*."""
    stream = FileStream(xaml_path, FileMode.Open)
    try:
        root = XamlReader.Load(stream)
    finally:
        stream.Close()

    object.__setattr__(component, '_wpf', root)
    _bind_names(component, root)   # walks full logical tree from root


def _bind_names(component, element):
    """Recursively bind named WPF elements as Python attributes on *component*."""
    if element is None:
        return
    try:
        name = element.Name
        if name:
            setattr(component, name, element)
    except Exception:
        pass
    try:
        for child in LogicalTreeHelper.GetChildren(element):
            _bind_names(component, child)
    except Exception:
        pass
