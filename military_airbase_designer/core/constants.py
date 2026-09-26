"""Shared standards tables and colours.

Sources: UFC 3-260-04 (markings), UFC 3-535-01 (lighting/signs), FAA AC 150/5340-1M,
FAA AC 150/5345-44L, ICAO Annex 14 Vol I. Feet are converted with ``units.ft``.
Module-specific tables (approach light layouts, sign sizes, structure dimensions)
live next to the code that uses them; this file holds what several modules share.
"""
from __future__ import annotations

from .units import ft, inch

ADDON_ID = "military_airbase_designer"
PREFIX = "MAD_"            # object / mesh name prefix
MAT_PREFIX = "M_MAD_"      # material name prefix
TEX_PREFIX = "T_MAD_"      # texture file prefix
MARKING_Z = 0.004          # paint meshes float 4 mm above the pavement (no z-fighting, invisible step)
BORDER_Z = 0.003           # black borders sit 1 mm under the paint
DECAL_Z = 0.002            # stains / rubber decals (if any)
PAVEMENT_THICKNESS = 0.40  # visible pavement edge drop to the dirt strip (m)
DIRT_STEP = -0.03          # infield sits 3 cm below pavement edge

# --------------------------------------------------------------------------- paint colours (sRGB hex, FS 595 chips)
PAINT_WHITE = "#F2F3EA"        # White 37925 (retro-reflective, glass beads)
PAINT_WHITE_WEATHERED = "#D9D8CE"
PAINT_YELLOW = "#F7C431"       # Yellow 33538 fresh airfield yellow
PAINT_YELLOW_WEATHERED = "#C9A24A"
PAINT_RED = "#A83A34"          # Red 31136 (slightly brighter than chip for paint)
PAINT_BLACK = "#2A2B2C"        # Black 37038 border paint
PAINT_ORANGE = "#C14429"       # Orange 12197 (compass rose, obstruction)
PAINT_GREEN = "#476753"        # Green 34108
ROAD_WHITE = "#E8E8E2"
ROAD_YELLOW = "#E9B824"

# --------------------------------------------------------------------------- light colours (emissive, as seen)
LIGHT_WHITE = "#FFF4E0"        # aviation white (incandescent)
LIGHT_WHITE_LED = "#FFFFFF"
LIGHT_FLASHER = "#DCEBFF"      # xenon sequenced flashers / REIL
LIGHT_GREEN = "#22E58A"        # aviation green (teal)
LIGHT_RED = "#FF2A00"
LIGHT_YELLOW = "#FFB400"       # amber caution zone / RGL
LIGHT_BLUE = "#2A5BFF"         # taxiway edge
LIGHT_FLOOD = "#FFF1D6"        # apron floodlights (HPS/LED)

# --------------------------------------------------------------------------- sign colours
SIGN_RED = "#C41E3A"
SIGN_YELLOW = "#F5C400"
SIGN_BLACK = "#111111"
SIGN_WHITE = "#F4F4F4"
AGM_YELLOW = "#FFD000"

# --------------------------------------------------------------------------- fixture / prop body colours
FIXTURE_YELLOW = "#E8A317"     # elevated fixture housings (FAA yellow)
FIXTURE_ALUMINUM = "#A5A8AA"
FIXTURE_CAST = "#8A8C8E"       # in-pavement fixture tops
OBSTRUCTION_ORANGE = "#FF4F00"
OBSTRUCTION_WHITE = "#F2F2F2"
WIND_CONE_ORANGE = "#FF6A00"
CONCRETE_BASE = "#9A9A96"
GALVANIZED = "#9DA3A6"
NATO_GREEN = "#4B5320"
NATO_GREY = "#7A7F7B"
NATO_TAN = "#A69A78"
DOOR_RED = "#B22222"

# --------------------------------------------------------------------------- runway marking dimensions (UFC 3-260-04 / FAA)
class MK:
    """Marking geometry (metres). Names mirror the standard so they can be audited."""
    numeral_height = ft(60)
    numeral_gap = ft(15)
    threshold_stripe_len = ft(150)
    threshold_stripe_w = ft(5.75)
    threshold_pitch = ft(11.5)
    threshold_center_gap = ft(11.5)     # inner edges of the central pair at ±5.75 ft
    threshold_start = ft(20)
    threshold_edge_margin = ft(4)
    threshold_max_stripes = 16
    threshold_bar_w = ft(10)
    designator_gap_after_stripes = ft(40)
    designator_letter_gap = ft(20)
    centerline_start_gap = ft(40)
    centerline_stripe = ft(120)
    centerline_gap = ft(80)
    centerline_w = ft(3)
    centerline_w_visual = ft(1)
    aiming_start = ft(1020)
    aiming_len = ft(150)
    aiming_w = ft(30)
    aiming_inner = ft(72)               # inner-edge separation
    tdz_starts = (ft(520), ft(1520), ft(2020), ft(2520), ft(3020))
    tdz_groups = (3, 2, 2, 1, 1)
    tdz_bar_len = ft(75)
    tdz_bar_w = ft(6)
    tdz_bar_gap = ft(5)
    tdz_inner = ft(72)
    tdz_omit_from_midpoint = ft(1000)   # UFC: omit pairs within 1,000 ft of the midpoint
    edge_w = ft(3)
    edge_w_narrow = ft(1.5)
    edge_start = ft(20)
    edge_inner_special = ft(194)        # 200/300 ft runways
    chevron_w = ft(3)
    chevron_first_apex = -ft(50)        # inside the threshold (only the outboard part painted)
    chevron_pitch = ft(100)
    chevron_pitch_short = ft(50)        # overrun < 250 ft
    chevron_margin = ft(5)
    shoulder_stripe_w = ft(3)
    shoulder_stripe_pitch = ft(100)
    shoulder_stripe_max_len = ft(25)
    shoulder_stripe_margin = ft(5)
    arresting_disc_d = ft(10)
    arresting_pitch = ft(25)
    arresting_first = ft(12.5)
    arresting_clearance = ft(1)
    taxi_cl_w = inch(6)
    taxi_cl_w_wide = inch(12)
    taxi_lead_on_offset = ft(3)         # from the near edge of the runway CL stripe
    taxi_lead_on_straight = ft(200)
    taxi_lead_on_radius = ft(150)
    taxi_edge_w = inch(6)
    taxi_edge_gap = inch(6)
    taxi_edge_dash = ft(15)
    taxi_edge_dash_gap = ft(25)
    hold_a_line = inch(6)
    hold_a_line_faa = inch(12)
    hold_a_dash = ft(3)
    hold_a_dash_gap = ft(3)
    hold_b_line = ft(2)
    hold_b_spacing = ft(4)
    hold_b_rung = ft(1)
    hold_b_rung_gap = ft(1)
    hold_b_rung_pitch = ft(10)
    enhanced_len = ft(150)
    enhanced_dash = ft(9)
    enhanced_gap = ft(3)
    enhanced_offset = inch(6)
    enhanced_last_dash = ft(6)
    sphps_text_h = ft(12)
    sphps_margin = inch(15)
    sphps_before_hold = ft(3)
    sphps_lateral = ft(6)
    black_border = inch(6)
    stop_bar_w = ft(2)
    road_edge_w = inch(6)
    road_lane_dash = ft(15)
    road_lane_gap = ft(25)
    parking_stop_len = ft(3)
    parking_stop_w = ft(1)
    hydrant_square = ft(6)
    spot_number_h = ft(4)
    helipad_h_ratio = 0.6
    compass_stripe_w = inch(6)
    compass_stripe_len = ft(25)
    nonmovement_w = inch(6)
    interrupt_threshold = ft(5)
    interrupt_other = ft(3)


# --------------------------------------------------------------------------- lighting geometry shared with signage
class LT:
    edge_offset = ft(8)              # runway edge lights outboard of the full-strength edge (2-10 ft)
    edge_spacing = ft(200)
    caution_zone = ft(2000)          # amber last 2,000 ft
    threshold_spacing = ft(10)
    threshold_wingbar = 8            # lights per wing bar group (incl.)
    end_spacing = ft(5)
    end_count_per_side = 5
    centerline_spacing = ft(50)
    centerline_alt_zone = ft(3000)   # alternating red/white from 3,000 to 1,000 ft
    centerline_red_zone = ft(1000)
    centerline_offset = ft(2)        # offset from the paint stripe
    tdz_spacing = ft(100)
    tdz_length = ft(3000)
    tdz_inner = ft(36)               # inner light of the barrette from CL
    tdz_barrette_spacing = ft(5)
    papi_offset_edge = ft(50)
    papi_box_spacing = ft(30)
    papi_distance = ft(1000)
    taxi_edge_offset = ft(5)
    taxi_edge_spacing = ft(200)
    taxi_edge_spacing_curve = ft(50)
    taxi_cl_spacing = ft(50)
    rgl_offset = ft(20)              # wig-wags outboard of the hold line
    flood_mast_h = 24.0
    beacon_h = 18.0
    sign_offset_edge = ft(20)
    sign_offset_min = ft(10)
    sign_offset_max = ft(35)
    rdr_offset_edge = ft(50)         # 50-75 ft from the runway edge
    rdr_interval = ft(1000)
    agm_offset_edge = ft(50)
    fixture_elevated_h = 0.36        # 14 in
    fixture_inpavement_d = 0.30      # 12 in


# --------------------------------------------------------------------------- sign sizes (FAA AC 150/5345-44L Table 3-1)
SIGN_SIZES = {
    # size: (legend height, panel height, mounting height of top)
    1: (inch(12), inch(18), inch(30)),
    2: (inch(15), inch(24), inch(36)),
    3: (inch(18), inch(30), inch(42)),
    4: (inch(24), inch(48), inch(60)),
    5: (inch(30), inch(48), inch(60)),
}


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    """'#RRGGBB' -> sRGB floats 0..1."""
    h = h.lstrip('#')
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(h: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b = hex_to_rgb(h)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), alpha)
