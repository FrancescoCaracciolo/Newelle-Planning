"""
NewellePlanning Extension
Manus-style persistent markdown planning for Newelle

Implements the workflow pattern from planning-with-files:
- Context Window = RAM (volatile, limited)  
- Filesystem = Disk (persistent, unlimited)
- Anything important gets written to disk
"""

from .extensions import NewelleExtension
from .tools import Tool, ToolResult
from .handlers.extra_settings import ExtraSettings
from .handlers import TabButtonDescription
import os
import datetime
import re
import threading
import difflib
from gi.repository import Gtk, Gio, GLib, Pango


# Templates for planning files
TASK_PLAN_TEMPLATE = """# Task Plan: {task_name}
Created: {date}
Status: In Progress

## Objective
{objective}

## Phases
{phases}

## Decisions
<!-- Record key decisions here -->

## Error Log
<!-- Log any errors or failed attempts here -->

## Notes
<!-- Additional notes and observations -->
"""

FINDINGS_TEMPLATE = """# Findings: {task_name}
Created: {date}

## Research Notes
<!-- Store research and findings here instead of context -->

## Technical Decisions
<!-- Record technical choices and rationale -->

## Key Discoveries

## References

## Code Snippets
<!-- Important code patterns found -->
"""

PROGRESS_TEMPLATE = """# Progress Log: {task_name}
Created: {date}

## Status Check
<!-- 5-Question Check when resuming -->
1. What is the current specific goal?
2. What has been done so far?
3. What is the immediate next step?
4. What information is missing?
5. Are there any errors or blockers?

## Session Log

### {date}
- Started task
- Created planning files

## Test Results
<!-- Document test outcomes here -->
| Test | Result | Notes |
|------|--------|-------|

## Next Steps
"""


# ===================== MODERN GTK WIDGETS =====================

class PlanningStatusWidget(Gtk.Box):
    """Modern widget displaying planning status with glass-morphism style"""
    
    def __init__(self, task_name: str, objective: str, completed: int, total: int, 
                 errors: int, planning_dir: str, has_findings: bool, has_progress: bool):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        # Custom CSS for modern look
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .planning-header {
                background: linear-gradient(135deg, alpha(@accent_bg_color, 0.15), alpha(@accent_bg_color, 0.05));
                border-radius: 12px 12px 0 0;
                padding: 16px;
            }
            .planning-progress-ring {
                font-size: 28px;
                font-weight: 700;
                color: @accent_color;
            }
            .planning-stat-box {
                background: alpha(@card_bg_color, 0.5);
                border-radius: 8px;
                padding: 12px;
                min-width: 80px;
            }
            .planning-stat-value {
                font-size: 20px;
                font-weight: 700;
            }
            .planning-stat-label {
                font-size: 11px;
                opacity: 0.7;
            }
            .planning-file-chip {
                background: alpha(@success_bg_color, 0.15);
                border-radius: 16px;
                padding: 4px 12px;
                font-size: 12px;
            }
            .planning-file-chip.missing {
                background: alpha(@warning_bg_color, 0.15);
            }
            .error-badge {
                background: alpha(@error_bg_color, 0.2);
                color: @error_color;
                border-radius: 12px;
                padding: 2px 10px;
                font-weight: 600;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header with gradient
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.add_css_class("planning-header")
        
        # Left side - Progress circle
        progress_pct = int(completed / total * 100) if total > 0 else 0
        
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        progress_box.set_valign(Gtk.Align.CENTER)
        
        # Circular progress indicator using level bar styled as ring
        progress_circle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        progress_circle.set_halign(Gtk.Align.CENTER)
        
        pct_label = Gtk.Label(label=f"{progress_pct}%")
        pct_label.add_css_class("planning-progress-ring")
        progress_circle.append(pct_label)
        
        progress_sublabel = Gtk.Label(label="complete")
        progress_sublabel.add_css_class("caption")
        progress_sublabel.add_css_class("dim-label")
        progress_circle.append(progress_sublabel)
        
        progress_box.append(progress_circle)
        header.append(progress_box)
        
        # Center - Task info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        info_box.set_valign(Gtk.Align.CENTER)
        
        # Task name with icon
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        plan_icon = Gtk.Image.new_from_icon_name("view-list-bullet-symbolic")
        plan_icon.set_pixel_size(20)
        plan_icon.add_css_class("accent")
        title_row.append(plan_icon)
        
        title_label = Gtk.Label(label=task_name, xalign=0)
        title_label.add_css_class("title-3")
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_row.append(title_label)
        
        # Error badge if any
        if errors > 0:
            error_badge = Gtk.Label(label=f"⚠ {errors}")
            error_badge.add_css_class("error-badge")
            error_badge.set_margin_start(8)
            title_row.append(error_badge)
        
        info_box.append(title_row)
        
        # Objective
        if objective:
            obj_label = Gtk.Label(
                label=objective[:120] + ("..." if len(objective) > 120 else ""),
                xalign=0,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                lines=2,
            )
            obj_label.set_ellipsize(Pango.EllipsizeMode.END)
            obj_label.add_css_class("dim-label")
            info_box.append(obj_label)
        
        header.append(info_box)
        self.append(header)
        
        # Progress bar
        progress_bar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        progress_bar_box.set_margin_start(16)
        progress_bar_box.set_margin_end(16)
        progress_bar_box.set_margin_top(8)
        
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_fraction(completed / total if total > 0 else 0)
        progress_bar.set_show_text(False)
        if progress_pct >= 100:
            progress_bar.add_css_class("success")
        progress_bar_box.append(progress_bar)
        
        items_label = Gtk.Label(label=f"{completed} of {total} items completed", xalign=0)
        items_label.add_css_class("caption")
        items_label.add_css_class("dim-label")
        progress_bar_box.append(items_label)
        
        self.append(progress_bar_box)
        
        # Stats row
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True)
        stats_box.set_margin_start(16)
        stats_box.set_margin_end(16)
        stats_box.set_margin_top(16)
        stats_box.set_margin_bottom(16)
        
        # Completed stat
        stats_box.append(self._create_stat_box(str(completed), "Completed", "emblem-default-symbolic"))
        
        # Pending stat
        pending = total - completed
        stats_box.append(self._create_stat_box(str(pending), "Pending", "emblem-synchronizing-symbolic"))
        
        # Errors stat
        stats_box.append(self._create_stat_box(str(errors), "Errors", "dialog-warning-symbolic"))
        
        self.append(stats_box)
        
        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        
        # Files row
        files_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        files_box.set_margin_start(16)
        files_box.set_margin_end(16)
        files_box.set_margin_top(12)
        files_box.set_margin_bottom(12)
        
        files_label = Gtk.Label(label="Files:")
        files_label.add_css_class("dim-label")
        files_box.append(files_label)
        
        # File chips
        files_box.append(self._create_file_chip("task_plan.md", True))
        files_box.append(self._create_file_chip("findings.md", has_findings))
        files_box.append(self._create_file_chip("progress.md", has_progress))
        
        # Spacer
        spacer = Gtk.Box(hexpand=True)
        files_box.append(spacer)
        
        # Open folder button
        if planning_dir:
            open_btn = Gtk.Button()
            open_btn.set_icon_name("folder-open-symbolic")
            open_btn.add_css_class("flat")
            open_btn.add_css_class("circular")
            open_btn.set_tooltip_text(f"Open {planning_dir}")
            open_btn.connect("clicked", lambda b: GLib.spawn_command_line_async(f"xdg-open '{planning_dir}'"))
            files_box.append(open_btn)
        
        self.append(files_box)
    
    def _create_stat_box(self, value: str, label: str, icon_name: str) -> Gtk.Box:
        """Create a stat display box"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("planning-stat-box")
        box.set_halign(Gtk.Align.CENTER)
        
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        icon.add_css_class("dim-label")
        box.append(icon)
        
        value_label = Gtk.Label(label=value)
        value_label.add_css_class("planning-stat-value")
        box.append(value_label)
        
        label_widget = Gtk.Label(label=label)
        label_widget.add_css_class("planning-stat-label")
        box.append(label_widget)
        
        return box
    
    def _create_file_chip(self, filename: str, exists: bool) -> Gtk.Box:
        """Create a file status chip"""
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chip.add_css_class("planning-file-chip")
        if not exists:
            chip.add_css_class("missing")
        
        icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic" if exists else "list-remove-symbolic")
        icon.set_pixel_size(12)
        chip.append(icon)
        
        label = Gtk.Label(label=filename)
        chip.append(label)
        
        return chip


class TodoListWidget(Gtk.Box):
    """Modern todo list widget with interactive checkboxes"""
    
    def __init__(self, todos: list, on_toggle_callback=None, title: str = "Tasks"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_size_request(-1, 200)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        self.on_toggle = on_toggle_callback
        
        # Custom CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .todo-header {
                padding: 12px 16px;
                background: alpha(@accent_bg_color, 0.08);
            }
            .todo-item {
                padding: 8px 16px;
                border-bottom: 1px solid alpha(@borders, 0.3);
            }
            .todo-item:last-child {
                border-bottom: none;
            }
            .todo-completed .todo-text {
                text-decoration: line-through;
                opacity: 0.5;
            }
            .phase-header {
                background: alpha(@view_bg_color, 0.5);
                padding: 6px 16px;
                font-weight: 600;
                font-size: 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        completed = sum(1 for t in todos if t.get('completed', False))
        total = len(todos)
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("todo-header")
        
        icon = Gtk.Image.new_from_icon_name("checkbox-checked-symbolic")
        icon.set_pixel_size(18)
        icon.add_css_class("accent")
        header.append(icon)
        
        title_label = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_label.add_css_class("heading")
        header.append(title_label)
        
        count_label = Gtk.Label(label=f"{completed}/{total}")
        count_label.add_css_class("caption")
        count_label.add_css_class("dim-label")
        header.append(count_label)
        
        self.append(header)
        
        # Group by phase
        phases = {}
        for todo in todos:
            phase = todo.get('phase', 'Other')
            if phase not in phases:
                phases[phase] = []
            phases[phase].append(todo)
        
        # Create todo items
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        for phase, phase_todos in phases.items():
            if len(phases) > 1 and phase:
                # Phase header
                phase_header = Gtk.Label(label=phase, xalign=0)
                phase_header.add_css_class("phase-header")
                list_box.append(phase_header)
            
            for todo in phase_todos:
                row = self._create_todo_row(todo)
                list_box.append(row)
        
        # Scrolled container for long lists
        if len(todos) > 5:
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_max_content_height(300)
            scrolled.set_propagate_natural_height(True)
            scrolled.set_child(list_box)
            self.append(scrolled)
        else:
            self.append(list_box)
    
    def _create_todo_row(self, todo: dict) -> Gtk.Box:
        """Create a single todo item row"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("todo-item")
        if todo.get('completed'):
            row.add_css_class("todo-completed")
        
        # Checkbox
        check = Gtk.CheckButton()
        check.set_active(todo.get('completed', False))
        check.set_valign(Gtk.Align.CENTER)
        if self.on_toggle:
            check.connect("toggled", lambda btn, txt=todo.get('text', ''): self.on_toggle(txt, btn.get_active()))
        row.append(check)
        
        # Text
        text_label = Gtk.Label(
            label=todo.get('text', ''),
            xalign=0,
            hexpand=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        text_label.add_css_class("todo-text")
        row.append(text_label)
        
        return row


class FindingWidget(Gtk.Box):
    """Widget for displaying a single finding"""
    
    def __init__(self, title: str, content: str, category: str = None, timestamp: str = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        # CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .finding-header {
                background: linear-gradient(90deg, alpha(@blue_3, 0.12), transparent);
                padding: 12px 16px;
                border-radius: 12px 12px 0 0;
            }
            .finding-content {
                padding: 12px 16px;
            }
            .finding-category {
                background: alpha(@accent_bg_color, 0.15);
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("finding-header")
        
        icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        icon.set_pixel_size(18)
        icon.add_css_class("warning")
        header.append(icon)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("heading")
        title_box.append(title_label)
        
        if timestamp:
            time_label = Gtk.Label(label=timestamp, xalign=0)
            time_label.add_css_class("caption")
            time_label.add_css_class("dim-label")
            title_box.append(time_label)
        
        header.append(title_box)
        
        if category:
            cat_label = Gtk.Label(label=category)
            cat_label.add_css_class("finding-category")
            header.append(cat_label)
        
        self.append(header)
        
        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.add_css_class("finding-content")
        
        content_label = Gtk.Label(
            label=content[:500] + ("..." if len(content) > 500 else ""),
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
        )
        content_box.append(content_label)
        
        self.append(content_box)


class ErrorLogWidget(Gtk.Box):
    """Widget for displaying logged errors"""
    
    def __init__(self, error: str, context: str = "", timestamp: str = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .error-widget-header {
                background: linear-gradient(90deg, alpha(@error_bg_color, 0.2), transparent);
                padding: 12px 16px;
                border-radius: 12px 12px 0 0;
            }
            .error-content {
                padding: 12px 16px;
                background: alpha(@error_bg_color, 0.05);
            }
            .error-reminder {
                background: alpha(@warning_bg_color, 0.1);
                border-radius: 8px;
                padding: 8px 12px;
                margin: 8px 16px 12px 16px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("error-widget-header")
        
        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_pixel_size(20)
        icon.add_css_class("error")
        header.append(icon)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        
        title_label = Gtk.Label(label="Error Logged", xalign=0)
        title_label.add_css_class("heading")
        title_box.append(title_label)
        
        if timestamp:
            time_label = Gtk.Label(label=timestamp, xalign=0)
            time_label.add_css_class("caption")
            time_label.add_css_class("dim-label")
            title_box.append(time_label)
        
        header.append(title_box)
        self.append(header)
        
        # Error content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content_box.add_css_class("error-content")
        
        error_label = Gtk.Label(
            label=error,
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
        )
        error_label.add_css_class("error")
        content_box.append(error_label)
        
        if context:
            ctx_label = Gtk.Label(label=f"Context: {context}", xalign=0, wrap=True)
            ctx_label.add_css_class("dim-label")
            content_box.append(ctx_label)
        
        self.append(content_box)
        
        # Reminder
        reminder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reminder.add_css_class("error-reminder")
        
        remind_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        remind_icon.set_pixel_size(16)
        reminder.append(remind_icon)
        
        remind_label = Gtk.Label(
            label="Never repeat failures — track attempts, mutate approach!",
            xalign=0,
            wrap=True,
        )
        remind_label.add_css_class("caption")
        reminder.append(remind_label)
        
        self.append(reminder)


class PlanCreatedWidget(Gtk.Box):
    """Celebratory widget shown when a plan is created"""
    
    def __init__(self, task_name: str, objective: str, planning_dir: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .plan-created-header {
                background: linear-gradient(135deg, alpha(@success_bg_color, 0.2), alpha(@accent_bg_color, 0.1));
                padding: 20px;
                border-radius: 12px 12px 0 0;
            }
            .plan-created-files {
                padding: 16px;
            }
            .file-row {
                padding: 8px 12px;
                border-radius: 8px;
                background: alpha(@card_bg_color, 0.5);
                margin-bottom: 8px;
            }
            .plan-tip {
                background: alpha(@accent_bg_color, 0.1);
                border-radius: 8px;
                padding: 12px;
                margin: 0 16px 16px 16px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("plan-created-header")
        header.set_halign(Gtk.Align.CENTER)
        
        check_icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic")
        check_icon.set_pixel_size(48)
        check_icon.add_css_class("success")
        header.append(check_icon)
        
        success_label = Gtk.Label(label="Plan Created!")
        success_label.add_css_class("title-1")
        header.append(success_label)
        
        task_label = Gtk.Label(label=task_name)
        task_label.add_css_class("title-3")
        task_label.add_css_class("accent")
        header.append(task_label)
        
        if objective:
            obj_label = Gtk.Label(
                label=objective[:100] + ("..." if len(objective) > 100 else ""),
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
            obj_label.add_css_class("dim-label")
            header.append(obj_label)
        
        self.append(header)
        
        # Files created
        files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        files_box.add_css_class("plan-created-files")
        
        files_label = Gtk.Label(label="Files Created:", xalign=0)
        files_label.add_css_class("heading")
        files_label.set_margin_bottom(12)
        files_box.append(files_label)
        
        files_info = [
            ("user-bookmarks-symbolic", "task_plan.md", "Phases, tasks, and major decisions"),
            ("system-search-symbolic", "findings.md", "Research, discoveries, technical choices"),
            ("document-edit-symbolic", "progress.md", "Session log, tests, 5-question check"),
        ]
        
        for icon_name, filename, description in files_info:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("file-row")
            
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(20)
            icon.add_css_class("accent")
            row.append(icon)
            
            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            
            name_label = Gtk.Label(label=filename, xalign=0)
            name_label.add_css_class("heading")
            text_box.append(name_label)
            
            desc_label = Gtk.Label(label=description, xalign=0)
            desc_label.add_css_class("caption")
            desc_label.add_css_class("dim-label")
            text_box.append(desc_label)
            
            row.append(text_box)
            files_box.append(row)
        
        self.append(files_box)
        
        # Tip
        tip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tip_box.add_css_class("plan-tip")
        
        tip_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        tip_icon.set_pixel_size(16)
        tip_icon.add_css_class("accent")
        tip_box.append(tip_icon)
        
        tip_label = Gtk.Label(
            label="💡 Remember the 2-Action Rule: Save findings after every 2 view/browser operations!",
            xalign=0,
            wrap=True,
        )
        tip_label.add_css_class("caption")
        tip_box.append(tip_label)
        
        self.append(tip_box)


class EmptyPlanWidget(Gtk.Box):
    """Widget shown when no plan exists"""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.add_css_class("card")
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_halign(Gtk.Align.CENTER)
        
        self.set_margin_start(24)
        self.set_margin_end(24)
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        
        icon = Gtk.Image.new_from_icon_name("document-new-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        self.append(icon)
        
        title = Gtk.Label(label="No Active Plan")
        title.add_css_class("title-2")
        title.add_css_class("dim-label")
        self.append(title)
        
        hint = Gtk.Label(
            label="Use create_plan to start a new planning session",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        self.append(hint)
        
        # Core principle box
        principle_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        principle_box.set_margin_top(8)
        
        principle_label = Gtk.Label(label="Core Principle:", xalign=0)
        principle_label.add_css_class("heading")
        principle_box.append(principle_label)
        
        lines = [
            "Context Window = RAM (volatile)",
            "Filesystem = Disk (persistent)",
            "→ Anything important goes to disk"
        ]
        for line in lines:
            line_label = Gtk.Label(label=line, xalign=0)
            line_label.add_css_class("caption")
            principle_box.append(line_label)
        
        self.append(principle_box)


class PlanningMiniApp(Gtk.Box):
    """Mini App widget showing real-time planning status"""
    
    def __init__(self, extension):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.extension = extension
        self._poll_source_id = None
        self._last_data_hash = None
        
        # Custom CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .planning-mini-header {
                background: linear-gradient(135deg, alpha(@accent_bg_color, 0.15), alpha(@blue_3, 0.08));
                padding: 16px;
            }
            .planning-mini-content {
                background: alpha(@view_bg_color, 0.3);
            }
            .planning-mini-status {
                background: alpha(@success_bg_color, 0.15);
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 12px;
            }
            .planning-mini-status.active {
                background: alpha(@warning_bg_color, 0.2);
            }
            .file-content-box {
                background: alpha(@card_bg_color, 0.5);
                border-radius: 8px;
                padding: 12px;
                margin: 8px;
            }
            .file-content-text {
                font-family: monospace;
                font-size: 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("planning-mini-header")
        
        icon = Gtk.Image.new_from_icon_name("view-list-bullet-symbolic")
        icon.set_pixel_size(24)
        icon.add_css_class("accent")
        header.append(icon)
        
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        
        title_label = Gtk.Label(label="Planning Session", xalign=0)
        title_label.add_css_class("title-3")
        title_box.append(title_label)
        
        self.subtitle_label = Gtk.Label(label="No active plan", xalign=0)
        self.subtitle_label.add_css_class("caption")
        self.subtitle_label.add_css_class("dim-label")
        title_box.append(self.subtitle_label)
        
        header.append(title_box)
        
        # Status indicator
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.status_box.add_css_class("planning-mini-status")
        
        self.status_label = Gtk.Label(label="Idle")
        self.status_box.append(self.status_label)
        
        header.append(self.status_box)
        
        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.add_css_class("circular")
        refresh_btn.set_tooltip_text("Refresh planning status")
        refresh_btn.connect("clicked", lambda b: self._update_content())
        header.append(refresh_btn)
        
        # Open folder button
        self.open_btn = Gtk.Button()
        self.open_btn.set_icon_name("folder-open-symbolic")
        self.open_btn.add_css_class("flat")
        self.open_btn.add_css_class("circular")
        self.open_btn.set_tooltip_text("Open planning directory")
        self.open_btn.connect("clicked", self._on_open_folder)
        header.append(self.open_btn)
        
        self.append(header)
        
        # Content area with scrolling
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content_box.add_css_class("planning-mini-content")
        self.content_box.set_vexpand(True)
        
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        scrolled.set_child(self.content_box)
        self.append(scrolled)
        
        # Start polling when widget is realized
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
    
    def _on_realize(self, widget):
        """Start polling when widget becomes visible"""
        self._start_polling()
        self._update_content()
    
    def _on_unrealize(self, widget):
        """Stop polling when widget is hidden"""
        self._stop_polling()
    
    def _start_polling(self):
        """Start polling for planning updates"""
        if self._poll_source_id is None:
            self._poll_source_id = GLib.timeout_add(2000, self._poll_planning)
    
    def _stop_polling(self):
        """Stop polling"""
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None
    
    def _poll_planning(self):
        """Poll for planning file changes"""
        try:
            self._update_content()
        except Exception as e:
            print(f"Planning polling error: {e}")
        return True  # Continue polling
    
    def _on_open_folder(self, button):
        """Open planning directory"""
        planning_dir = self.extension._get_planning_dir()
        if os.path.exists(planning_dir):
            GLib.spawn_command_line_async(f"xdg-open '{planning_dir}'")
    
    def _update_content(self):
        """Update the content display"""
        try:
            data = self.extension._get_planning_data()
            
            # Create hash to check if data changed
            data_hash = f"{data['task_name']}_{data['completed']}_{data['total']}_{data['errors']}"
            if data_hash == self._last_data_hash:
                return
            self._last_data_hash = data_hash
            
            # Clear existing content
            child = self.content_box.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.content_box.remove(child)
                child = next_child
            
            if not data['exists']:
                # Show empty state
                self.subtitle_label.set_label("No active plan")
                self.status_label.set_label("Idle")
                self.status_box.remove_css_class("active")
                
                empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                empty_box.set_halign(Gtk.Align.CENTER)
                empty_box.set_valign(Gtk.Align.CENTER)
                empty_box.set_vexpand(True)
                empty_box.set_margin_top(48)
                empty_box.set_margin_bottom(48)
                
                empty_icon = Gtk.Image.new_from_icon_name("document-new-symbolic")
                empty_icon.set_pixel_size(48)
                empty_icon.add_css_class("dim-label")
                empty_box.append(empty_icon)
                
                empty_label = Gtk.Label(label="No Active Plan")
                empty_label.add_css_class("title-4")
                empty_label.add_css_class("dim-label")
                empty_box.append(empty_label)
                
                empty_hint = Gtk.Label(
                    label="Use create_plan tool to start planning",
                    wrap=True,
                    justify=Gtk.Justification.CENTER,
                )
                empty_hint.add_css_class("caption")
                empty_hint.add_css_class("dim-label")
                empty_box.append(empty_hint)
                
                self.content_box.append(empty_box)
            else:
                # Show planning status
                self.subtitle_label.set_label(data['task_name'])
                
                if data['completed'] < data['total']:
                    self.status_label.set_label("In Progress")
                    self.status_box.add_css_class("active")
                else:
                    self.status_label.set_label("Complete")
                    self.status_box.remove_css_class("active")
                
                # Progress section
                progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                progress_box.set_margin_start(16)
                progress_box.set_margin_end(16)
                progress_box.set_margin_top(16)
                
                progress_pct = int(data['completed'] / data['total'] * 100) if data['total'] > 0 else 0
                
                progress_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                progress_title = Gtk.Label(label="Progress", xalign=0, hexpand=True)
                progress_title.add_css_class("heading")
                progress_header.append(progress_title)
                
                progress_label = Gtk.Label(label=f"{data['completed']}/{data['total']} ({progress_pct}%)")
                progress_label.add_css_class("caption")
                progress_header.append(progress_label)
                
                progress_box.append(progress_header)
                
                progress_bar = Gtk.ProgressBar()
                progress_bar.set_fraction(data['completed'] / data['total'] if data['total'] > 0 else 0)
                if progress_pct >= 100:
                    progress_bar.add_css_class("success")
                progress_box.append(progress_bar)
                
                if data['errors'] > 0:
                    error_label = Gtk.Label(label=f"⚠ {data['errors']} error(s) logged", xalign=0)
                    error_label.add_css_class("error")
                    error_label.add_css_class("caption")
                    progress_box.append(error_label)
                
                self.content_box.append(progress_box)
                
                # Objective section
                if data['objective']:
                    obj_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    obj_box.add_css_class("file-content-box")
                    
                    obj_title = Gtk.Label(label="Objective", xalign=0)
                    obj_title.add_css_class("heading")
                    obj_box.append(obj_title)
                    
                    obj_text = Gtk.Label(
                        label=data['objective'][:300] + ("..." if len(data['objective']) > 300 else ""),
                        xalign=0,
                        wrap=True,
                        wrap_mode=Pango.WrapMode.WORD_CHAR,
                    )
                    obj_text.add_css_class("dim-label")
                    obj_box.append(obj_text)
                    
                    self.content_box.append(obj_box)
                
                # Todos section
                if data['todos']:
                    todos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    todos_box.add_css_class("file-content-box")
                    
                    todos_title = Gtk.Label(label="Tasks", xalign=0)
                    todos_title.add_css_class("heading")
                    todos_box.append(todos_title)
                    
                    # Group by phase
                    phases = {}
                    for todo in data['todos']:
                        phase = todo.get('phase', 'Other')
                        if phase not in phases:
                            phases[phase] = []
                        phases[phase].append(todo)
                    
                    for phase, phase_todos in phases.items():
                        if len(phases) > 1 and phase:
                            phase_label = Gtk.Label(label=phase, xalign=0)
                            phase_label.add_css_class("caption")
                            phase_label.add_css_class("accent")
                            phase_label.set_margin_top(8)
                            todos_box.append(phase_label)
                        
                        for todo in phase_todos:
                            todo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                            todo_row.set_margin_top(4)
                            
                            check_icon = Gtk.Image.new_from_icon_name(
                                "emblem-default-symbolic" if todo['completed'] else "radio-symbolic"
                            )
                            check_icon.set_pixel_size(16)
                            if todo['completed']:
                                check_icon.add_css_class("success")
                            else:
                                check_icon.add_css_class("dim-label")
                            todo_row.append(check_icon)
                            
                            todo_text = Gtk.Label(
                                label=todo['text'],
                                xalign=0,
                                hexpand=True,
                                wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR,
                            )
                            if todo['completed']:
                                todo_text.add_css_class("dim-label")
                            todo_row.append(todo_text)
                            
                            todos_box.append(todo_row)
                    
                    self.content_box.append(todos_box)
                
                # Files status
                files_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                files_box.set_margin_start(16)
                files_box.set_margin_end(16)
                files_box.set_margin_top(8)
                files_box.set_margin_bottom(16)
                
                files_label = Gtk.Label(label="Files:", xalign=0)
                files_label.add_css_class("caption")
                files_label.add_css_class("dim-label")
                files_box.append(files_label)
                
                for fname, exists in [("task_plan.md", True), 
                                       ("findings.md", data['has_findings']),
                                       ("progress.md", data['has_progress'])]:
                    chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                    chip.add_css_class("planning-file-chip")
                    if not exists:
                        chip.add_css_class("missing")
                    
                    chip_icon = Gtk.Image.new_from_icon_name(
                        "emblem-default-symbolic" if exists else "list-remove-symbolic"
                    )
                    chip_icon.set_pixel_size(12)
                    chip.append(chip_icon)
                    
                    chip_label = Gtk.Label(label=fname)
                    chip_label.add_css_class("caption")
                    chip.append(chip_label)
                    
                    files_box.append(chip)
                
                self.content_box.append(files_box)
                
        except Exception as e:
            print(f"Error updating planning content: {e}")


# ===================== EXTENSION CLASS =====================

class NewellePlanningExtension(NewelleExtension):
    """
    NewellePlanning - Manus-style persistent markdown planning
    """
    
    name = "Newelle Planning"
    id = "newelle_planning"
    
    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting(
                "planning_directory", 
                "Planning Directory", 
                "Directory where planning files are stored ('.' for project root)",
                "."
            ),
            ExtraSettings.ScaleSetting(
                "max_plan_length",
                "Max Plan Length",
                "Maximum characters to include from planning files",
                4000, 1000, 20000, 0
            ),
            ExtraSettings.ToggleSetting(
                "mini_app_enabled",
                "Mini App",
                "Show a tab with real-time planning status",
                True
            ),
        ]
    
    def add_tab_menu_entries(self) -> list:
        if self.get_setting("mini_app_enabled") is False:
            return []
        return [
            TabButtonDescription("Planning", "view-list-bullet-symbolic", lambda x, y: self._open_planning_tab(x))
        ]
    
    def _open_planning_tab(self, button):
        """Open the planning mini app tab"""
        widget = PlanningMiniApp(self)
        widget.set_vexpand(True)
        widget.set_hexpand(True)
        tab = self.ui_controller.add_tab(widget)
        tab.set_title("Planning")
        tab.set_icon(Gio.ThemedIcon(name="view-list-bullet-symbolic"))
    
    def _get_planning_dir(self) -> str:
        base_dir = self.get_setting("planning_directory")
        if not base_dir:
            base_dir = "."
        if os.path.isabs(base_dir):
            return base_dir
        return os.path.join(os.getcwd(), base_dir)
    
    def _ensure_planning_dir(self) -> str:
        planning_dir = self._get_planning_dir()
        os.makedirs(planning_dir, exist_ok=True)
        return planning_dir
    
    def _truncate(self, text: str) -> str:
        maxlength = self.get_setting("max_plan_length") or 4000
        if len(text) > maxlength:
            return text[:maxlength] + f"\n... (truncated to {maxlength} characters)"
        return text
    
    def _get_date(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    def _file_path(self, filename: str) -> str:
        return os.path.join(self._get_planning_dir(), filename)
    
    def _parse_todos_from_plan(self, content: str) -> list:
        """Parse todo items from plan content"""
        todos = []
        current_phase = None
        
        for line in content.split('\n'):
            phase_match = re.match(r'^###\s+(.+)$', line)
            if phase_match:
                current_phase = phase_match.group(1)
            
            todo_match = re.match(r'^-\s+\[([ x])\]\s+(.+)$', line)
            if todo_match:
                completed = todo_match.group(1) == 'x'
                text = todo_match.group(2)
                todos.append({
                    'text': text,
                    'completed': completed,
                    'phase': current_phase
                })
        
        return todos
    
    def _get_planning_data(self) -> dict:
        """Get all planning data"""
        planning_dir = self._get_planning_dir()
        data = {
            'task_name': 'No Plan',
            'objective': '',
            'todos': [],
            'completed': 0,
            'total': 0,
            'errors': 0,
            'exists': False,
            'planning_dir': planning_dir,
            'has_findings': os.path.exists(self._file_path("findings.md")),
            'has_progress': os.path.exists(self._file_path("progress.md")),
        }
        
        plan_path = self._file_path("task_plan.md")
        if os.path.exists(plan_path):
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            data['exists'] = True
            
            if "# Task Plan:" in content:
                task_line = content.split('\n')[0]
                data['task_name'] = task_line.replace("# Task Plan:", "").strip()
            
            obj_match = re.search(r'## Objective\n(.+?)(?=\n##|\Z)', content, re.DOTALL)
            if obj_match:
                data['objective'] = obj_match.group(1).strip()
            
            data['todos'] = self._parse_todos_from_plan(content)
            data['completed'] = sum(1 for t in data['todos'] if t['completed'])
            data['total'] = len(data['todos'])
            data['errors'] = content.count('### Error at')
        
        return data
    
    # === Core Operations ===
    
    def create_plan(self, task_name: str, objective: str, phases: list[str] = None) -> str:
        try:
            planning_dir = self._ensure_planning_dir()
            date = self._get_date()
            
            if not phases:
                phases_content = """### Phase 1: Planning
- [ ] Define requirements
- [ ] Identify dependencies
- [ ] Create initial plan
"""
            else:
                phases_content = ""
                for i, phase in enumerate(phases, 1):
                    phases_content += f"### Phase {i}: {phase}\n- [ ] \n\n"
                phases_content = phases_content.strip()
            
            task_plan_content = TASK_PLAN_TEMPLATE.format(
                task_name=task_name, date=date, objective=objective, phases=phases_content
            )
            with open(self._file_path("task_plan.md"), 'w', encoding='utf-8') as f:
                f.write(task_plan_content)
            
            findings_content = FINDINGS_TEMPLATE.format(task_name=task_name, date=date)
            with open(self._file_path("findings.md"), 'w', encoding='utf-8') as f:
                f.write(findings_content)
            
            progress_content = PROGRESS_TEMPLATE.format(task_name=task_name, date=date)
            with open(self._file_path("progress.md"), 'w', encoding='utf-8') as f:
                f.write(progress_content)
            
            return f"✅ Created planning files in {planning_dir}\n\nTask: {task_name}\nObjective: {objective}"
            
        except Exception as e:
            return f"❌ Error creating plan: {str(e)}"
    
    def read_plan(self, start_char: int = 0) -> str:
        try:
            plan_path = self._file_path("task_plan.md")
            if not os.path.exists(plan_path):
                return "⚠️ No task_plan.md found. Use create_plan to start."
            
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if start_char > 0:
                if start_char >= len(content):
                    return "⚠️ End of file reached."
                content = content[start_char:]
                
            return self._truncate(content)
        except Exception as e:
            return f"❌ Error reading plan: {str(e)}"
    
    def update_plan(self, section: str, content: str) -> str:
        try:
            plan_path = self._file_path("task_plan.md")
            if not os.path.exists(plan_path):
                return "⚠️ No task_plan.md found."
            
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan_content = f.read()
            
            section_marker = f"## {section}"
            if section_marker in plan_content:
                section_start = plan_content.index(section_marker) + len(section_marker)
                next_section = plan_content.find("\n## ", section_start)
                
                if next_section == -1:
                    new_content = plan_content[:section_start] + f"\n{content}\n"
                else:
                    new_content = (
                        plan_content[:section_start] + 
                        f"\n{content}\n" + 
                        plan_content[next_section:]
                    )
                
                with open(plan_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return f"✅ Updated section '{section}'"
            else:
                new_content = plan_content.rstrip() + f"\n\n## {section}\n{content}\n"
                with open(plan_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return f"✅ Added new section '{section}'"
                
        except Exception as e:
            return f"❌ Error updating plan: {str(e)}"
    
    def mark_complete(self, phase_or_item: str) -> str:
        try:
            plan_path = self._file_path("task_plan.md")
            if not os.path.exists(plan_path):
                return "⚠️ No task_plan.md found."
            
            with open(plan_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            target = phase_or_item.strip().lower()
            best_match_idx = -1
            
            # Helper to check if line is an incomplete task
            def is_incomplete_task(line):
                return re.match(r'^\s*[-*]\s*\[ \]', line) is not None

            # 1. Exact match search
            for i, line in enumerate(lines):
                if not is_incomplete_task(line):
                    continue
                
                task_text = re.sub(r'^\s*[-*]\s*\[ \]\s*', '', line).strip()
                if task_text == phase_or_item.strip():
                    best_match_idx = i
                    break
            
            # 2. Substring/Fuzzy match if no exact match
            if best_match_idx == -1:
                candidates = []
                for i, line in enumerate(lines):
                    if not is_incomplete_task(line):
                        continue
                        
                    task_text = re.sub(r'^\s*[-*]\s*\[ \]\s*', '', line).strip()
                    task_lower = task_text.lower()
                    
                    # Substring match (high priority)
                    if target in task_lower:
                        candidates.append((i, 0.9, task_text))
                        continue
                        
                    # Fuzzy match
                    ratio = difflib.SequenceMatcher(None, target, task_lower).ratio()
                    if ratio > 0.8: # Threshold
                        candidates.append((i, ratio, task_text))
                
                # Sort by score desc
                if candidates:
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    best_match_idx = candidates[0][0]
                    
            if best_match_idx != -1:
                line = lines[best_match_idx]
                # Replace [ ] with [x] preserving indentation and bullet style
                new_line = re.sub(r'(\s*[-*]\s*)\[ \]', r'\1[x]', line, count=1)
                lines[best_match_idx] = new_line
                
                with open(plan_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                
                task_text = re.sub(r'^\s*[-*]\s*\[ \]\s*', '', line).strip()
                return f"✅ Marked as complete: {task_text}"
            
            return f"⚠️ Item '{phase_or_item}' not found (or already completed)"
                
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def add_todo(self, item: str, phase: str = None) -> str:
        try:
            plan_path = self._file_path("task_plan.md")
            if not os.path.exists(plan_path):
                return "⚠️ No task_plan.md found."
            
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_item = f"- [ ] {item}"
            
            if phase:
                # Try exact match first
                phase_marker = f"### {phase}"
                phase_start = -1
                
                if phase_marker in content:
                    phase_start = content.index(phase_marker)
                else:
                    # Try to find fuzzy match (e.g. "Phase 1: Analysis" matching "Analysis")
                    # We look for a line starting with ### containing the phase name
                    pattern = re.compile(rf"^###\s+.*{re.escape(phase)}", re.MULTILINE | re.IGNORECASE)
                    match = pattern.search(content)
                    if match:
                        phase_marker = match.group(0)
                        phase_start = match.start()
                
                if phase_start != -1:
                    next_section = content.find("\n###", phase_start + 1)
                    next_h2 = content.find("\n## ", phase_start + 1)
                    
                    # Find the earliest next section (subsection or main section)
                    possible_ends = [p for p in [next_section, next_h2] if p != -1]
                    insert_pos = min(possible_ends) if possible_ends else len(content)
                    
                    content = content[:insert_pos].rstrip() + f"\n{new_item}\n" + content[insert_pos:]
                else:
                    content = content.rstrip() + f"\n\n### {phase}\n{new_item}\n"
            else:
                if "## Phases" in content:
                    notes_pos = content.find("## Notes")
                    error_pos = content.find("## Error Log")
                    insert_pos = min(p for p in [notes_pos, error_pos, len(content)] if p > 0)
                    content = content[:insert_pos].rstrip() + f"\n{new_item}\n\n" + content[insert_pos:]
                else:
                    content = content.rstrip() + f"\n\n## Tasks\n{new_item}\n"
            
            with open(plan_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return f"✅ Added todo: {item}" + (f" (Phase: {phase})" if phase else "")
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def save_finding(self, title: str, content: str, category: str = "Key Discoveries") -> str:
        try:
            findings_path = self._file_path("findings.md")
            if not os.path.exists(findings_path):
                self._ensure_planning_dir()
                with open(findings_path, 'w', encoding='utf-8') as f:
                    f.write(FINDINGS_TEMPLATE.format(task_name="Task", date=self._get_date()))
            
            with open(findings_path, 'r', encoding='utf-8') as f:
                findings_content = f.read()
            
            timestamp = self._get_date()
            new_finding = f"\n### {title}\n*{timestamp}*\n\n{content}\n"
            
            category_marker = f"## {category}"
            if category_marker in findings_content:
                category_pos = findings_content.index(category_marker) + len(category_marker)
                next_h2 = findings_content.find("\n## ", category_pos)
                
                if next_h2 == -1:
                    findings_content = findings_content.rstrip() + new_finding
                else:
                    findings_content = (
                        findings_content[:next_h2].rstrip() + 
                        new_finding + "\n" + 
                        findings_content[next_h2:]
                    )
            else:
                findings_content = findings_content.rstrip() + f"\n\n## {category}{new_finding}"
            
            with open(findings_path, 'w', encoding='utf-8') as f:
                f.write(findings_content)
            
            return f"✅ Saved finding: '{title}'"
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def read_findings(self, start_char: int = 0) -> str:
        try:
            findings_path = self._file_path("findings.md")
            if not os.path.exists(findings_path):
                return "⚠️ No findings.md found."
            
            with open(findings_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if start_char > 0:
                if start_char >= len(content):
                    return "⚠️ End of file reached."
                content = content[start_char:]
                
            return self._truncate(content)
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def log_progress(self, entry: str, include_timestamp: bool = True) -> str:
        try:
            progress_path = self._file_path("progress.md")
            if not os.path.exists(progress_path):
                self._ensure_planning_dir()
                with open(progress_path, 'w', encoding='utf-8') as f:
                    f.write(PROGRESS_TEMPLATE.format(task_name="Task", date=self._get_date()))
            
            with open(progress_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            timestamp = f"[{self._get_date()}] " if include_timestamp else ""
            new_entry = f"- {timestamp}{entry}"
            
            if "## Session Log" in content:
                session_pos = content.index("## Session Log") + len("## Session Log")
                content = content[:session_pos] + f"\n{new_entry}" + content[session_pos:]
            else:
                content = content.rstrip() + f"\n\n## Session Log\n{new_entry}\n"
            
            with open(progress_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return f"✅ Logged: {entry}"
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def log_error(self, error: str, context: str = "") -> str:
        try:
            plan_path = self._file_path("task_plan.md")
            if not os.path.exists(plan_path):
                return "⚠️ No task_plan.md found."
            
            with open(plan_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            timestamp = self._get_date()
            error_entry = f"\n### Error at {timestamp}\n**Error:** {error}\n"
            if context:
                error_entry += f"**Context:** {context}\n"
            
            if "## Error Log" in content:
                error_pos = content.index("## Error Log") + len("## Error Log")
                next_h2 = content.find("\n## ", error_pos)
                
                if next_h2 == -1:
                    content = content.rstrip() + error_entry
                else:
                    content = content[:next_h2].rstrip() + error_entry + "\n" + content[next_h2:]
            else:
                content = content.rstrip() + f"\n\n## Error Log{error_entry}"
            
            with open(plan_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.log_progress(f"ERROR: {error}")
            
            return f"⚠️ Logged error: {error}"
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def get_status(self) -> str:
        try:
            data = self._get_planning_data()
            
            if not data['exists']:
                return "📋 No active planning session. Use create_plan to start."
            
            progress_pct = int(data['completed'] / data['total'] * 100) if data['total'] > 0 else 0
            
            return f"""📋 **{data['task_name']}**

Progress: {data['completed']}/{data['total']} ({progress_pct}%)
Errors: {data['errors']}
Directory: {data['planning_dir']}

Files: task_plan.md ✅ | findings.md {'✅' if data['has_findings'] else '❌'} | progress.md {'✅' if data['has_progress'] else '❌'}"""
            
        except Exception as e:
            return f"❌ Error: {str(e)}"
    
    def cleanup_plan(self) -> str:
        try:
            planning_dir = self._get_planning_dir()
            
            if not os.path.exists(planning_dir):
                return "⚠️ No planning directory."
            
            files_removed = []
            for filename in ["task_plan.md", "findings.md", "progress.md"]:
                filepath = self._file_path(filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
                    files_removed.append(filename)
            
            try:
                os.rmdir(planning_dir)
                return f"✅ Cleaned up: {', '.join(files_removed)}"
            except OSError:
                return f"✅ Cleaned up: {', '.join(files_removed)} (directory kept)"
                
        except Exception as e:
            return f"❌ Error: {str(e)}"

    def check_plan_integrity(self) -> str:
        try:
            data = self._get_planning_data()
            if not data['exists']:
                return "⚠️ No plan found."
            
            issues = []
            
            # Check files
            if not data['has_findings']:
                issues.append("- Missing findings.md")
            if not data['has_progress']:
                issues.append("- Missing progress.md")
                
            # Check tasks
            pending = data['total'] - data['completed']
            if pending > 0:
                issues.append(f"- {pending} tasks pending in task_plan.md")
            
            # Check progress log
            progress_path = self._file_path("progress.md")
            if os.path.exists(progress_path):
                with open(progress_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if "- " not in content:
                        issues.append("- progress.md seems empty (no bullet points)")
            
            if not issues:
                return f"✅ Plan Integrity Check Passed!\n- All files present\n- All {data['total']} tasks completed\n- Progress logged"
            
            return "⚠️ Plan Incomplete / Issues Found:\n" + "\n".join(issues)
            
        except Exception as e:
            return f"❌ Error checking integrity: {str(e)}"
    
    # === Tool Wrappers with Widgets ===
    
    def _tool_create_plan(self, task_name: str, objective: str, phases: list[str] = None) -> ToolResult:
        result = ToolResult()
        
        # Run operation synchronously
        output = self.create_plan(task_name, objective, phases)
        result.set_output(output)
        
        # Create widget after operation completes
        widget = PlanCreatedWidget(task_name, objective, self._get_planning_dir())
        result.set_widget(widget)
        
        # Auto-open mini app tab if enabled
        if self.get_setting("mini_app_enabled") is not False:
            GLib.idle_add(self._open_planning_tab, None)
        
        return result
    
    def _restore_create_plan(self, task_name: str, objective: str, phases: list[str] = None, tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        
        widget = PlanCreatedWidget(task_name, objective, self._get_planning_dir())
        result.set_widget(widget)
        result.set_output(output)
        return result
    
    def _tool_get_status(self) -> ToolResult:
        result = ToolResult()
        
        # Get data and create widget synchronously
        data = self._get_planning_data()
        
        if data['exists']:
            widget = PlanningStatusWidget(
                task_name=data['task_name'],
                objective=data['objective'],
                completed=data['completed'],
                total=data['total'],
                errors=data['errors'],
                planning_dir=data['planning_dir'],
                has_findings=data['has_findings'],
                has_progress=data['has_progress'],
            )
        else:
            widget = EmptyPlanWidget()
        result.set_widget(widget)
        
        # Get status output
        output = self.get_status()
        result.set_output(output)
        
        return result
    
    def _restore_get_status(self, tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        data = self._get_planning_data()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else self.get_status()
        
        if data['exists']:
            widget = PlanningStatusWidget(
                task_name=data['task_name'],
                objective=data['objective'],
                completed=data['completed'],
                total=data['total'],
                errors=data['errors'],
                planning_dir=data['planning_dir'],
                has_findings=data['has_findings'],
                has_progress=data['has_progress'],
            )
        else:
            widget = EmptyPlanWidget()
        
        result.set_widget(widget)
        result.set_output(output)
        return result
    
    def _tool_mark_complete(self, phase_or_item: str) -> ToolResult:
        result = ToolResult()
        
        # Run operation synchronously to avoid race conditions with multiple calls
        output = self.mark_complete(phase_or_item)
        result.set_output(output)
        
        # Create widget after operation completes (shows updated state)
        data = self._get_planning_data()
        if data['todos']:
            widget = TodoListWidget(data['todos'], title="Tasks")
            result.set_widget(widget)
        
        return result
    
    def _restore_mark_complete(self, phase_or_item: str, tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        
        data = self._get_planning_data()
        if data['todos']:
            widget = TodoListWidget(data['todos'], title="Tasks")
            result.set_widget(widget)
        
        result.set_output(output)
        return result
    
    def _tool_add_todo(self, item: str, phase: str = None) -> ToolResult:
        result = ToolResult()
        
        # Run operation synchronously to avoid race conditions
        output = self.add_todo(item, phase)
        result.set_output(output)
        
        # Create widget after operation completes (shows updated state with new item)
        data = self._get_planning_data()
        if data['todos']:
            widget = TodoListWidget(data['todos'], title="Tasks")
            result.set_widget(widget)
        
        return result
    
    def _restore_add_todo(self, item: str, phase: str = None, tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        
        data = self._get_planning_data()
        if data['todos']:
            widget = TodoListWidget(data['todos'], title="Tasks")
            result.set_widget(widget)
        
        result.set_output(output)
        return result
    
    def _tool_save_finding(self, title: str, content: str, category: str = "Key Discoveries") -> ToolResult:
        result = ToolResult()
        
        # Create widget with provided data
        widget = FindingWidget(title, content, category, self._get_date())
        result.set_widget(widget)
        
        # Run operation synchronously for consistency
        output = self.save_finding(title, content, category)
        result.set_output(output)
        
        return result
    
    def _restore_save_finding(self, title: str, content: str, category: str = "Key Discoveries", tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        
        widget = FindingWidget(title, content, category)
        result.set_widget(widget)
        result.set_output(output)
        return result
    
    def _tool_log_error(self, error: str, context: str = "") -> ToolResult:
        result = ToolResult()
        
        # Create widget with provided data
        widget = ErrorLogWidget(error, context, self._get_date())
        result.set_widget(widget)
        
        # Run operation synchronously for consistency
        output = self.log_error(error, context)
        result.set_output(output)
        
        return result
    
    def _restore_log_error(self, error: str, context: str = "", tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        
        widget = ErrorLogWidget(error, context)
        result.set_widget(widget)
        result.set_output(output)
        return result
    
    def _tool_simple(self, func, *args, **kwargs) -> ToolResult:
        """Generic wrapper for tools without widgets"""
        result = ToolResult()
        
        # Run operation in thread
        def run():
            output = func(*args, **kwargs)
            result.set_output(output)
        
        thread = threading.Thread(target=run)
        thread.start()
        return result
    
    def _restore_simple(self, tool_uuid: str = None) -> ToolResult:
        result = ToolResult()
        output = self.ui_controller.get_tool_result_by_id(tool_uuid) if tool_uuid else None
        result.set_output(output)
        return result
    
    def get_tools(self) -> list:
        return [
            Tool(
                name="create_plan",
                description="Create a new task plan. Provide 'phases' list to customize steps (e.g. ['Analyze', 'Fix']).",
                func=self._tool_create_plan,
                title="Create Plan",
                restore_func=self._restore_create_plan,
                tools_group="Planning",
            ),
            Tool(
                name="read_plan",
                description="Read the current task_plan.md. Use 'start_char' to read from an offset if truncated.",
                func=lambda start_char=0: self._tool_simple(self.read_plan, start_char),
                title="Read Plan",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
            Tool(
                name="update_plan",
                description="Update a section in the task plan.",
                func=lambda section, content: self._tool_simple(self.update_plan, section, content),
                title="Update Plan",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
            Tool(
                name="mark_complete",
                description="Mark a checklist item as complete.",
                func=self._tool_mark_complete,
                title="Mark Complete",
                restore_func=self._restore_mark_complete,
                tools_group="Planning",
            ),
            Tool(
                name="add_todo",
                description="Add a new todo item. Optionally specify a phase.",
                func=self._tool_add_todo,
                title="Add Todo",
                restore_func=self._restore_add_todo,
                tools_group="Planning",
            ),
            Tool(
                name="save_finding",
                description="Save a research finding. Follow the 2-Action Rule!",
                func=self._tool_save_finding,
                title="Save Finding",
                restore_func=self._restore_save_finding,
                tools_group="Planning",
            ),
            Tool(
                name="read_findings",
                description="Read all saved findings. Use 'start_char' to read from an offset.",
                func=lambda start_char=0: self._tool_simple(self.read_findings, start_char),
                title="Read Findings",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
            Tool(
                name="log_progress",
                description="Log a progress entry to the session log.",
                func=lambda entry, include_timestamp=True: self._tool_simple(self.log_progress, entry, include_timestamp),
                title="Log Progress",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
            Tool(
                name="log_error",
                description="Log an error. ALWAYS log errors to avoid repeating failures!",
                func=self._tool_log_error,
                title="Log Error",
                restore_func=self._restore_log_error,
                tools_group="Planning",
            ),
            Tool(
                name="get_planning_status",
                description="Get planning status summary with visual progress.",
                func=self._tool_get_status,
                title="Planning Status",
                restore_func=self._restore_get_status,
                tools_group="Planning",
            ),
            Tool(
                name="check_plan_integrity",
                description="Verify if all tasks are complete and files exist (runs logic similar to check-complete.sh).",
                func=lambda: self._tool_simple(self.check_plan_integrity),
                title="Check Integrity",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
            Tool(
                name="cleanup_plan",
                description="Remove all planning files after completion.",
                func=lambda: self._tool_simple(self.cleanup_plan),
                title="Cleanup Plan",
                restore_func=self._restore_simple,
                tools_group="Planning",
            ),
        ]
    
    def get_additional_prompts(self) -> list:
        return [
            {
                "key": "newelle_planning",
                "setting_name": "newelle_planning_enabled",
                "title": "Newelle Planning Methodology",
                "description": "Enable Manus-style persistent markdown planning",
                "editable": True,
                "show_in_settings": True,
                "default": True,
                "text": """## Newelle Planning Methodology (v2.0)

For complex tasks (3+ steps), use the planning tools:

### Core Principle
- Context Window = RAM (volatile)
- Filesystem = Disk (persistent)
- → Anything important goes to disk.

### The 3-File Pattern
1. **task_plan.md** - Phases, todos, errors, major decisions
2. **findings.md** - Research, discoveries, technical decisions
3. **progress.md** - Session log, test results, status checks

### Key Rules
1. **Create Plan First** - Use `create_plan` before complex tasks.
2. **2-Action Rule** - Save findings after every 2 operations.
3. **Log ALL Errors** - Use `log_error` to avoid repetition.
4. **Re-read Before Decisions** - Use `read_plan` before major choices.
5. **Never Repeat Failures** - Check error log, mutate approach.
6. **5-Question Check** - When resuming, verify status in `progress.md`.
7. **Verify Completion** - Ensure all phases are marked complete."""
            }
        ]
