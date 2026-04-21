import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from PIL import Image, ImageTk, ImageDraw

# ==========================================
# CONFIGURATOR
# ==========================================
MAP_JSON        = "erlc_map.json"
MAP_IMAGE       = "fall_postals.jpg"
OUTPUT_PNG      = "debug_map_overlay.png"
GUI_SAVE_JSON   = "erlc_map_updated.json"

NODE_R          = 7       # radius for drawing nodes
SNAP_DIST       = 12      # pixel distance to "snap" click onto existing node

# ==========================================
# DRAW-ONLY MAP EXPORT
# ==========================================
def draw_map():
    with open(MAP_JSON) as f:
        data = json.load(f)

    nodes = data.get("nodes", {})
    edges = data.get("edges", [])

    img  = Image.open(MAP_IMAGE).convert("RGBA")
    draw = ImageDraw.Draw(img)

    for edge in edges:
        s, t = edge.get("source"), edge.get("target")
        if s in nodes and t in nodes:
            n1, n2 = nodes[s], nodes[t]
            if all(k in n for n in (n1, n2) for k in ("x", "y")):
                draw.line([(n1["x"], n1["y"]), (n2["x"], n2["y"])],
                          fill=(120, 120, 120), width=2)

    for node_id, info in nodes.items():
        x, y = info.get("x"), info.get("y")
        if x is None or y is None:
            continue
        if str(node_id).startswith("postal_"):
            color = (0, 200, 0)
        elif info.get("robable"):
            color = (255, 60, 60)
        else:
            color = (60, 120, 255)
        r = NODE_R
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    img.save(OUTPUT_PNG)
    print(f"[DONE] Saved overlay to {OUTPUT_PNG}")


# ==========================================
# GUI EDITOR
# ==========================================
class MapEditor:
    # Modes
    MODE_MOVE   = "move"
    MODE_ADD    = "add_node"
    MODE_EDGE   = "add_edge"
    MODE_DELETE = "delete"

    def __init__(self, root):
        self.root = root
        self.root.title("ERLC Map Editor")

        # ---- Load JSON data ----
        with open(MAP_JSON) as f:
            self.data = json.load(f)
        self.nodes = self.data.setdefault("nodes", {})
        self.edges = self.data.setdefault("edges", [])

        # ---- State ----
        self.mode          = self.MODE_MOVE
        self.drag_node_id  = None          # node being dragged
        self.drag_ox       = 0             # offset x during drag
        self.drag_oy       = 0             # offset y during drag
        self.edge_src      = None          # first node selected in EDGE mode
        self.selected_node = None          # highlighted node
        self.scale         = 1.0

        # Canvas item → node_id
        self.oval_to_node  = {}   # {canvas_oval_id: node_id}
        self.node_to_oval  = {}   # {node_id: canvas_oval_id}
        self.node_to_label = {}   # {node_id: canvas_text_id}

        # Canvas item → edge index
        self.line_to_edge  = {}   # {canvas_line_id: index into self.edges}

        # Canvas item → edge index
        self.line_to_edge  = {}   # {canvas_line_id: index into self.edges}

        # ---- Build UI ----
        self._build_toolbar()
        self._build_canvas()
        self._build_statusbar()

        self._load_image()
        self._render_all()

    # --------------------------------------------------
    # UI CONSTRUCTION
    # --------------------------------------------------
    def _build_toolbar(self):
        tb = tk.Frame(self.root, bd=1, relief=tk.RAISED)
        tb.pack(side=tk.TOP, fill=tk.X)

        btn_cfg = {"padx": 8, "pady": 4}

        self.mode_var = tk.StringVar(value=self.MODE_MOVE)

        for label, mode in [
            ("✋ Move",       self.MODE_MOVE),
            ("➕ Add Node",  self.MODE_ADD),
            ("🔗 Add Edge",  self.MODE_EDGE),
            ("🗑 Delete",    self.MODE_DELETE),
        ]:
            rb = tk.Radiobutton(
                tb, text=label, variable=self.mode_var,
                value=mode, indicatoron=False,
                command=self._on_mode_change,
                **btn_cfg
            )
            rb.pack(side=tk.LEFT)

        ttk.Separator(tb, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        tk.Button(tb, text="💾 Save JSON", command=self._save_json, **btn_cfg).pack(side=tk.LEFT)
        tk.Button(tb, text="🖼 Export PNG",  command=draw_map,        **btn_cfg).pack(side=tk.LEFT)

        ttk.Separator(tb, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        # zoom buttons
        tk.Button(tb, text="🔍+", command=lambda: self._zoom_btn(1.2),  **btn_cfg).pack(side=tk.LEFT)
        tk.Button(tb, text="🔍-", command=lambda: self._zoom_btn(1/1.2),**btn_cfg).pack(side=tk.LEFT)
        tk.Button(tb, text="⌂ Reset", command=self._reset_view,         **btn_cfg).pack(side=tk.LEFT)

        # legend
        legend = tk.Frame(tb)
        legend.pack(side=tk.RIGHT, padx=8)
        for color, text in [("red", "Robable"), ("blue", "POI"), ("gray", "Plain")]:
            tk.Label(legend, text="●", fg=color).pack(side=tk.LEFT)
            tk.Label(legend, text=text, padx=2).pack(side=tk.LEFT)

    def _build_canvas(self):
        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(frame, bg="#1a1a2e", cursor="crosshair")
        hbar = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        vbar = tk.Scrollbar(frame, orient=tk.VERTICAL,   command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        vbar.pack(side=tk.RIGHT,  fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bindings
        self.canvas.bind("<Button-1>",        self._on_left_click)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>",      self._on_scroll)       # Windows / Mac
        self.canvas.bind("<Button-4>",        self._on_scroll)       # Linux scroll up
        self.canvas.bind("<Button-5>",        self._on_scroll)       # Linux scroll down
        self.canvas.bind("<Button-2>",        self._pan_start)       # middle-click pan
        self.canvas.bind("<B2-Motion>",       self._pan_move)
        self.canvas.bind("<Button-3>",        self._on_right_click)  # right-click context
        self.canvas.bind("<Motion>",          self._on_mouse_move)

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Ready | Mode: Move")
        bar = tk.Label(self.root, textvariable=self.status_var,
                       bd=1, relief=tk.SUNKEN, anchor=tk.W)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    # --------------------------------------------------
    # IMAGE + INITIAL RENDER
    # --------------------------------------------------
    def _load_image(self):
        try:
            self.pil_img = Image.open(MAP_IMAGE).convert("RGB")
        except FileNotFoundError:
            # fallback blank canvas if image missing
            self.pil_img = Image.new("RGB", (3000, 3000), (30, 30, 50))

        self.tk_img = ImageTk.PhotoImage(self.pil_img)
        self.canvas_img_id = self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        w, h = self.pil_img.size
        self.canvas.configure(scrollregion=(0, 0, w, h))

    def _render_all(self):
        """Clear and redraw every edge then every node."""
        # Delete old node/edge canvas items (NOT the background image)
        self.canvas.delete("edge")
        self.canvas.delete("node")
        self.canvas.delete("label")
        self.oval_to_node.clear()
        self.node_to_oval.clear()
        self.node_to_label.clear()

        self._draw_all_edges()
        self._draw_all_nodes()

    def _draw_all_edges(self):
        self.line_to_edge.clear()
        for idx, edge in enumerate(self.edges):
            s, t = edge.get("source"), edge.get("target")
            if s in self.nodes and t in self.nodes:
                n1, n2 = self.nodes[s], self.nodes[t]
                if "x" in n1 and "y" in n1 and "x" in n2 and "y" in n2:
                    etype = edge.get("type", "local")
                    color = {
                        "highway":    "#ffdd57",
                        "arterial":   "#4fc3f7",
                        "industrial": "#ef9a9a",
                        "local":      "#90a4ae",
                        "winding":    "#ce93d8",
                    }.get(etype, "#90a4ae")
                    # Wide invisible line for easy clicking
                    hit_line = self.canvas.create_line(
                        n1["x"], n1["y"], n2["x"], n2["y"],
                        fill="", width=12, tags="edge"
                    )
                    vis_line = self.canvas.create_line(
                        n1["x"], n1["y"], n2["x"], n2["y"],
                        fill=color, width=2, tags="edge"
                    )
                    self.line_to_edge[hit_line] = idx
                    self.line_to_edge[vis_line] = idx

    def _draw_all_nodes(self):
        for node_id, info in self.nodes.items():
            if not isinstance(info, dict):
                continue
            x, y = info.get("x"), info.get("y")
            if x is None or y is None:
                continue
            self._create_node_item(node_id, x, y, info)

    def _create_node_item(self, node_id, x, y, info):
        r = NODE_R
        if str(node_id).startswith("postal_"):
            fill, outline = "#69f0ae", "#00c853"
        elif info.get("robable"):
            fill, outline = "#ef5350", "#b71c1c"
        elif info.get("poi"):
            fill, outline = "#42a5f5", "#0d47a1"
        else:
            fill, outline = "#78909c", "#455a64"

        oval = self.canvas.create_oval(
            x - r, y - r, x + r, y + r,
            fill=fill, outline=outline, width=2,
            tags="node"
        )
        label_text = info.get("label") or str(node_id)
        label = self.canvas.create_text(
            x + r + 3, y,
            text=label_text, anchor="w",
            fill="white", font=("Helvetica", 7),
            tags="label"
        )

        self.oval_to_node[oval]    = node_id
        self.node_to_oval[node_id] = oval
        self.node_to_label[node_id] = label

        self.canvas.tag_bind(oval,  "<Enter>", lambda e, nid=node_id: self._on_node_hover(e, nid))
        self.canvas.tag_bind(label, "<Enter>", lambda e, nid=node_id: self._on_node_hover(e, nid))

    # --------------------------------------------------
    # COORDINATE HELPERS
    # --------------------------------------------------
    def _canvas_xy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _find_node_at(self, cx, cy):
        """Return node_id of node closest to (cx,cy) within SNAP_DIST, else None."""
        best_id   = None
        best_dist = SNAP_DIST
        for node_id, info in self.nodes.items():
            if not isinstance(info, dict):
                continue
            nx_, ny_ = info.get("x"), info.get("y")
            if nx_ is None:
                continue
            d = ((cx - nx_) ** 2 + (cy - ny_) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_id   = node_id
        return best_id

    def _find_edge_at(self, cx, cy):
        """Return index into self.edges for the line closest to (cx,cy), else None."""
        items = self.canvas.find_closest(cx, cy)
        if not items:
            return None
        item = items[0]
        return self.line_to_edge.get(item)

    # --------------------------------------------------
    # MODE CHANGE
    # --------------------------------------------------
    def _on_mode_change(self):
        self.mode          = self.mode_var.get()
        self.edge_src      = None
        self.drag_node_id  = None
        self._clear_selection()
        mode_labels = {
            self.MODE_MOVE:   "Move nodes — drag to reposition",
            self.MODE_ADD:    "Add Node — click empty space",
            self.MODE_EDGE:   "Add Edge — click source, then target node",
            self.MODE_DELETE: "Delete — click a node or edge",
        }
        self._status(mode_labels.get(self.mode, ""))

    # --------------------------------------------------
    # MOUSE EVENTS
    # --------------------------------------------------
    def _on_left_click(self, event):
        cx, cy = self._canvas_xy(event)
        hit    = self._find_node_at(cx, cy)

        if self.mode == self.MODE_MOVE:
            if hit:
                self.drag_node_id = hit
                info = self.nodes[hit]
                self.drag_ox = cx - info["x"]
                self.drag_oy = cy - info["y"]

        elif self.mode == self.MODE_ADD:
            if hit:
                self._status(f"Node already here: {hit}")
                return
            self._add_node_dialog(cx, cy)

        elif self.mode == self.MODE_EDGE:
            if not hit:
                self._status("Click on an existing node to start edge.")
                return
            if self.edge_src is None:
                self.edge_src = hit
                self._highlight(hit)
                self._status(f"Edge source: {hit}  — now click target node")
            else:
                if hit == self.edge_src:
                    self._status("Same node — cancelled.")
                    self.edge_src = None
                    self._clear_selection()
                    return
                self._add_edge_dialog(self.edge_src, hit)
                self.edge_src = None
                self._clear_selection()

        elif self.mode == self.MODE_DELETE:
            if hit:
                self._delete_node(hit)
            else:
                # Check if an edge line was clicked
                edge_idx = self._find_edge_at(cx, cy)
                if edge_idx is not None:
                    self._delete_edge(edge_idx)

    def _on_drag(self, event):
        if self.mode != self.MODE_MOVE or not self.drag_node_id:
            return
        cx, cy    = self._canvas_xy(event)
        nx_       = round(cx - self.drag_ox)
        ny_       = round(cy - self.drag_oy)
        node_id   = self.drag_node_id
        self.nodes[node_id]["x"] = nx_
        self.nodes[node_id]["y"] = ny_

        # Update oval
        oval = self.node_to_oval.get(node_id)
        if oval:
            r = NODE_R
            self.canvas.coords(oval, nx_ - r, ny_ - r, nx_ + r, ny_ + r)
        # Update label
        lbl = self.node_to_label.get(node_id)
        if lbl:
            self.canvas.coords(lbl, nx_ + NODE_R + 3, ny_)

        # Redraw edges cheaply
        self.canvas.delete("edge")
        self._draw_all_edges()

        self._status(f"Dragging: {node_id}  →  x={nx_}, y={ny_}")

    def _on_release(self, event):
        if self.drag_node_id:
            self._status(f"Placed: {self.drag_node_id} at x={self.nodes[self.drag_node_id]['x']}, y={self.nodes[self.drag_node_id]['y']}")
        self.drag_node_id = None

    def _on_scroll(self, event):
        # Determine zoom direction
        if event.num == 4 or event.delta > 0:
            factor = 1.15
        else:
            factor = 1 / 1.15

        cx, cy = self._canvas_xy(event)
        self.canvas.scale("all", cx, cy, factor, factor)
        self.scale *= factor
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _zoom_btn(self, factor):
        cx = self.canvas.winfo_width()  / 2
        cy = self.canvas.winfo_height() / 2
        self.canvas.scale("all", cx, cy, factor, factor)
        self.scale *= factor
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _reset_view(self):
        self.canvas.scale("all", 0, 0, 1 / self.scale, 1 / self.scale)
        self.scale = 1.0
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_mouse_move(self, event):
        cx, cy = self._canvas_xy(event)
        hit    = self._find_node_at(cx, cy)
        if hit:
            info = self.nodes[hit]
            tip  = f"{hit}  |  POI: {info.get('poi','—')}  |  robable: {info.get('robable','—')}  |  x={info.get('x')} y={info.get('y')}"
            self._status(tip)
        else:
            self._status(f"x={int(cx)}, y={int(cy)}  |  Mode: {self.mode}")

    def _on_right_click(self, event):
        cx, cy = self._canvas_xy(event)
        hit    = self._find_node_at(cx, cy)
        if not hit:
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Edit: {hit}",         command=lambda: self._edit_node_dialog(hit))
        menu.add_command(label="Delete node",           command=lambda: self._delete_node(hit))
        menu.add_separator()
        menu.add_command(label="Start edge from here",  command=lambda: self._start_edge(hit))
        menu.tk_popup(event.x_root, event.y_root)

    def _on_right_click_edge(self, event, edge_idx):
        menu = tk.Menu(self.root, tearoff=0)
        edge = self.edges[edge_idx]
        menu.add_command(label=f"Delete edge: {edge.get('source')} → {edge.get('target')}", command=lambda: self._delete_edge(edge_idx))
        menu.tk_popup(event.x_root, event.y_root)

    def _on_node_hover(self, event, node_id):
        pass  # status handled by motion

    # --------------------------------------------------
    # SELECTION / HIGHLIGHT
    # --------------------------------------------------
    def _highlight(self, node_id):
        self._clear_selection()
        oval = self.node_to_oval.get(node_id)
        if oval:
            self.canvas.itemconfig(oval, outline="yellow", width=3)
        self.selected_node = node_id

    def _clear_selection(self):
        if self.selected_node:
            oval = self.node_to_oval.get(self.selected_node)
            if oval:
                info = self.nodes.get(self.selected_node, {})
                self.canvas.itemconfig(oval, outline="#455a64" if not info.get("robable") else "#b71c1c", width=2)
        self.selected_node = None

    # --------------------------------------------------
    # ADD NODE DIALOG
    # --------------------------------------------------
    def _add_node_dialog(self, cx, cy):
        win = tk.Toplevel(self.root)
        win.title("Add Node")
        win.grab_set()

        fields = {}

        def row(label, default=""):
            fr = tk.Frame(win); fr.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(fr, text=label, width=14, anchor="w").pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            tk.Entry(fr, textvariable=var, width=28).pack(side=tk.LEFT)
            return var

        id_var    = row("Node ID",   "")
        label_var = row("Label",     "")
        poi_var   = row("POI",       "")
        type_var  = row("Type",      "commercial")

        rob_var = tk.BooleanVar()
        fr2 = tk.Frame(win); fr2.pack(fill=tk.X, padx=8, pady=2)
        tk.Checkbutton(fr2, text="Robable", variable=rob_var).pack(side=tk.LEFT)

        coord_label = tk.Label(win, text=f"Will be placed at x={int(cx)}, y={int(cy)}", fg="gray")
        coord_label.pack(pady=4)

        def confirm():
            nid = id_var.get().strip()
            if not nid:
                messagebox.showerror("Error", "Node ID cannot be empty.", parent=win)
                return
            if nid in self.nodes:
                messagebox.showerror("Error", f"Node ID '{nid}' already exists.", parent=win)
                return

            entry = {
                "x":       round(cx),
                "y":       round(cy),
                "label":   label_var.get().strip() or None,
                "poi":     poi_var.get().strip()   or None,
                "type":    type_var.get().strip()  or None,
                "robable": rob_var.get()
            }
            # prune None
            entry = {k: v for k, v in entry.items() if v is not None}

            self.nodes[nid] = entry
            self._create_node_item(nid, round(cx), round(cy), entry)
            self._status(f"Added node: {nid}")
            win.destroy()

        btn_fr = tk.Frame(win); btn_fr.pack(pady=6)
        tk.Button(btn_fr, text="Add",    command=confirm).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_fr, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

    # --------------------------------------------------
    # EDIT NODE DIALOG
    # --------------------------------------------------
    def _edit_node_dialog(self, node_id):
        info = self.nodes.get(node_id, {})
        win  = tk.Toplevel(self.root)
        win.title(f"Edit Node: {node_id}")
        win.grab_set()

        def row(label, default=""):
            fr = tk.Frame(win); fr.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(fr, text=label, width=14, anchor="w").pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            tk.Entry(fr, textvariable=var, width=28).pack(side=tk.LEFT)
            return var

        label_var = row("Label",  info.get("label", "") or "")
        poi_var   = row("POI",    info.get("poi",   "") or "")
        type_var  = row("Type",   info.get("type",  "") or "")
        x_var     = row("X",      str(info.get("x", 0)))
        y_var     = row("Y",      str(info.get("y", 0)))

        rob_var = tk.BooleanVar(value=bool(info.get("robable", False)))
        fr2 = tk.Frame(win); fr2.pack(fill=tk.X, padx=8, pady=2)
        tk.Checkbutton(fr2, text="Robable", variable=rob_var).pack(side=tk.LEFT)

        def confirm():
            try:
                nx_ = float(x_var.get())
                ny_ = float(y_var.get())
            except ValueError:
                messagebox.showerror("Error", "X and Y must be numbers.", parent=win)
                return

            info["label"]   = label_var.get().strip() or None
            info["poi"]     = poi_var.get().strip()   or None
            info["type"]    = type_var.get().strip()  or None
            info["robable"] = rob_var.get()
            info["x"]       = round(nx_)
            info["y"]       = round(ny_)

            # Re-render this node only
            old_oval  = self.node_to_oval.pop(node_id, None)
            old_label = self.node_to_label.pop(node_id, None)
            if old_oval:  self.canvas.delete(old_oval)
            if old_label: self.canvas.delete(old_label)
            self._create_node_item(node_id, info["x"], info["y"], info)
            self.canvas.delete("edge")
            self._draw_all_edges()
            self._status(f"Updated: {node_id}")
            win.destroy()

        btn_fr = tk.Frame(win); btn_fr.pack(pady=6)
        tk.Button(btn_fr, text="Save",   command=confirm).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_fr, text="Cancel", command=win.destroy).pack(side=tk.LEFT, padx=4)

    # --------------------------------------------------
    # ADD EDGE DIALOG
    # --------------------------------------------------
    def _add_edge_dialog(self, src, tgt):
        win = tk.Toplevel(self.root)
        win.title(f"Add Edge: {src} → {tgt}")
        win.grab_set()

        def row(label, default=""):
            fr = tk.Frame(win); fr.pack(fill=tk.X, padx=8, pady=2)
            tk.Label(fr, text=label, width=14, anchor="w").pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            tk.Entry(fr, textvariable=var, width=28).pack(side=tk.LEFT)
            return var

        tk.Label(win, text=f"{src}  →  {tgt}", font=("Helvetica", 11, "bold")).pack(pady=6)

        road_var    = row("Road name", "")
        type_var    = row("Type",      "local")
        postals_var = row("Postals (comma sep)", "")

        oneway_var = tk.BooleanVar()
        fr2 = tk.Frame(win); fr2.pack(fill=tk.X, padx=8, pady=2)
        tk.Checkbutton(fr2, text="One-way", variable=oneway_var).pack(side=tk.LEFT)

        def confirm():
            postals_raw = postals_var.get().strip()
            postals     = [p.strip() for p in postals_raw.split(",") if p.strip()] if postals_raw else []

            edge = {
                "source":     src,
                "target":     tgt,
                "road":       road_var.get().strip() or "Unnamed Road",
                "type":       type_var.get().strip()  or "local",
                "is_one_way": oneway_var.get(),
                "metadata":   {"postals": postals}
            }

            # Check for duplicate
            for e in self.edges:
                if e.get("source") == src and e.get("target") == tgt:
                    if not messagebox.askyesno("Duplicate", "An edge between these nodes already exists. Add anyway?", parent=win):
                        win.destroy()
                        return
                    break

            self.edges.append(edge)
            self.canvas.delete("edge")
            self._draw_all_edges()
            self._status(f"Edge added: {src} → {tgt}")
            win.destroy()

        btn_fr = tk.Frame(win); btn_fr.pack(pady=6)
        tk.Button(btn_fr, text="Add Edge", command=confirm).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_fr, text="Cancel",   command=win.destroy).pack(side=tk.LEFT, padx=4)

    def _start_edge(self, node_id):
        self.mode_var.set(self.MODE_EDGE)
        self._on_mode_change()
        self.edge_src = node_id
        self._highlight(node_id)
        self._status(f"Edge source: {node_id}  — now click target node")

    # --------------------------------------------------
    # DELETE NODE
    # --------------------------------------------------
    def _delete_node(self, node_id):
        if not messagebox.askyesno("Delete", f"Delete node '{node_id}' and all its edges?"):
            return

        # Remove canvas items
        oval  = self.node_to_oval.pop(node_id, None)
        label = self.node_to_label.pop(node_id, None)
        if oval:  self.canvas.delete(oval)
        if label: self.canvas.delete(label)
        if oval in self.oval_to_node:
            del self.oval_to_node[oval]

        # Remove node from data
        del self.nodes[node_id]

        # Remove all edges referencing this node
        self.edges[:] = [
            e for e in self.edges
            if e.get("source") != node_id and e.get("target") != node_id
        ]

        # Redraw edges
        self.canvas.delete("edge")
        self._draw_all_edges()
        self._status(f"Deleted: {node_id}")

    # --------------------------------------------------
    # DELETE EDGE
    # --------------------------------------------------
    def _delete_edge(self, edge_idx):
        edge = self.edges[edge_idx]
        label = f"{edge.get('source')} → {edge.get('target')}  ({edge.get('road', 'unnamed')})"
        if not messagebox.askyesno("Delete Edge", f"Delete edge:\n{label}?"):
            return
        del self.edges[edge_idx]
        self.canvas.delete("edge")
        self._draw_all_edges()
        self._status(f"Deleted edge: {label}")

    # --------------------------------------------------
    # SAVE
    # --------------------------------------------------
    def _save_json(self):
        with open(GUI_SAVE_JSON, "w") as f:
            json.dump(self.data, f, indent=2)
        self._status(f"✅  Saved to {GUI_SAVE_JSON}")
        messagebox.showinfo("Saved", f"JSON saved to:\n{GUI_SAVE_JSON}")

    # --------------------------------------------------
    # STATUS BAR
    # --------------------------------------------------
    def _status(self, msg):
        self.status_var.set(msg)


# ==========================================
# ENTRY POINT
# ==========================================
def launch_gui_editor():
    root = tk.Tk()
    root.geometry("1400x900")
    app  = MapEditor(root)
    root.mainloop()


if __name__ == "__main__":
    mode = input("draw or gui: ").strip().lower()
    if mode == "gui":
        launch_gui_editor()
    else:
        draw_map()