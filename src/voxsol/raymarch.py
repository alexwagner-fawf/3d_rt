"""numba kernels: terrain-following ray marching for pulse accumulation and light transmittance.

Height above ground (hag) is z minus the bilinearly interpolated DTM. Canopy voxels exist for
h0 <= hag < h0 + nz*dz; below h0 the understory module takes over, hag < 0 is terrain.
Directions are unit vectors (east, north, up).
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

# march termination codes
SKY, TERRAIN, EDGE, OPAQUE, HORIZON = 0, 1, 2, 3, 4


@njit(cache=True, inline="always")
def bilinear(a, x0, y0, res, x, y):
    ny, nx = a.shape
    fx = (x - x0) / res - 0.5
    fy = (y - y0) / res - 0.5
    ix = int(math.floor(fx))
    iy = int(math.floor(fy))
    if ix < 0:
        ix, tx = 0, 0.0
    elif ix > nx - 2:
        ix, tx = nx - 2, 1.0
    else:
        tx = fx - ix
    if iy < 0:
        iy, ty = 0, 0.0
    elif iy > ny - 2:
        iy, ty = ny - 2, 1.0
    else:
        ty = fy - iy
    return ((1 - tx) * (1 - ty) * a[iy, ix] + tx * (1 - ty) * a[iy, ix + 1]
            + (1 - tx) * ty * a[iy + 1, ix] + tx * ty * a[iy + 1, ix + 1])


@njit(cache=True, parallel=True)
def bilinear_many(a, x0, y0, res, x, y):
    out = np.empty(x.size)
    for i in prange(x.size):
        out[i] = bilinear(a, x0, y0, res, x[i], y[i])
    return out


# --------------------------------------------------------------------------------------------
# Light transmittance (upward rays from targets)
# --------------------------------------------------------------------------------------------

@njit(cache=True)
def march_up(px, py, pz, ux, uy, uz, lam, gx0, gy0, dx, h0, dz,
             dtm, tx0, ty0, tres, top, cs, global_top, ds, tau_max):
    """Optical depth along an upward ray. Returns (tau, code).

    `top` is a coarse (cell size cs) grid of the highest absolute z of canopy or terrain in and
    around each cell; above it the ray skips to the next coarse cell.
    """
    ny, nx, nz = lam.shape
    mny, mnx = top.shape
    xmax = gx0 + nx * dx
    ymax = gy0 + ny * dx
    htop = h0 + nz * dz
    tau = 0.0
    x, y, z = px, py, pz
    horiz = math.hypot(ux, uy)
    cx = min(max(int((x - gx0) / cs), 0), mnx - 1)
    cy = min(max(int((y - gy0) / cs), 0), mny - 1)
    for _ in range(10_000_000):
        if z >= global_top:
            return tau, SKY
        if x < gx0 or x >= xmax or y < gy0 or y >= ymax:
            # truncated only if the ray left below the canopy/terrain top of the last cell
            return tau, (EDGE if z < top[cy, cx] else SKY)
        cx = min(max(int((x - gx0) / cs), 0), mnx - 1)
        cy = min(max(int((y - gy0) / cs), 0), mny - 1)
        if z > top[cy, cx]:
            if horiz < 1e-9:
                return tau, SKY
            # distance (along the ray) to the exit of the coarse cell in xy
            t = 1e30
            if ux > 1e-12:
                t = min(t, (gx0 + (cx + 1) * cs - x) / ux)
            elif ux < -1e-12:
                t = min(t, (gx0 + cx * cs - x) / ux)
            if uy > 1e-12:
                t = min(t, (gy0 + (cy + 1) * cs - y) / uy)
            elif uy < -1e-12:
                t = min(t, (gy0 + cy * cs - y) / uy)
            t = max(t, 0.0) + 1e-4
            x += ux * t
            y += uy * t
            z += uz * t
            continue
        xm = x + 0.5 * ds * ux
        ym = y + 0.5 * ds * uy
        zm = z + 0.5 * ds * uz
        hag = zm - bilinear(dtm, tx0, ty0, tres, xm, ym)
        if hag < 0.0:
            return tau, TERRAIN
        if h0 <= hag < htop:
            ix = int((xm - gx0) / dx)
            iy = int((ym - gy0) / dx)
            if 0 <= ix < nx and 0 <= iy < ny:
                tau += lam[iy, ix, int((hag - h0) / dz)] * ds
                if tau > tau_max:
                    return tau, OPAQUE
        x += ux * ds
        y += uy * ds
        z += uz * ds
    return tau, SKY


@njit(cache=True, parallel=True)
def transmittance_kernel(tx, ty, tz, dirs, lam, gx0, gy0, dx, h0, dz,
                         dtm, dx0, dy0, dres, top, cs, global_top, ds, tau_max,
                         hor, hx0, hy0, hres, haz_step, use_horizon):
    """Transmittance T[i, j] from target i towards direction j, plus per-target edge flags."""
    n = tx.size
    m = dirs.shape[0]
    T = np.empty((n, m), dtype=np.float32)
    edge = np.zeros((n, m), dtype=np.bool_)
    hny, hnx, naz = hor.shape
    for i in prange(n):
        hix = min(max(int((tx[i] - hx0) / hres), 0), hnx - 1)
        hiy = min(max(int((ty[i] - hy0) / hres), 0), hny - 1)
        for j in range(m):
            ux, uy, uz = dirs[j, 0], dirs[j, 1], dirs[j, 2]
            if uz <= 0.0:
                T[i, j] = 0.0
                continue
            if use_horizon:
                az = math.degrees(math.atan2(ux, uy)) % 360.0
                el = math.degrees(math.asin(min(uz, 1.0)))
                if el < hor[hiy, hix, int(az / haz_step) % naz]:
                    T[i, j] = 0.0
                    continue
            tau, code = march_up(tx[i], ty[i], tz[i], ux, uy, uz, lam, gx0, gy0, dx, h0, dz,
                                 dtm, dx0, dy0, dres, top, cs, global_top, ds, tau_max)
            if code == TERRAIN or code == OPAQUE:
                T[i, j] = 0.0
            else:
                T[i, j] = math.exp(-tau)
                edge[i, j] = code == EDGE
    return T, edge


# --------------------------------------------------------------------------------------------
# Pulse accumulation (downward beams, tiled so that tiles write disjoint voxels)
# --------------------------------------------------------------------------------------------

@njit(cache=True, parallel=True)
def pulse_start_params(origin, direc, s_end, dtm, dx0, dy0, dres, htop, step):
    """Ray parameter where each pulse enters the canopy layer (hag < htop), marching back from the end."""
    n = s_end.size
    s_start = np.empty(n)
    for p in prange(n):
        s = s_end[p]
        while s > 0.0:
            x = origin[p, 0] + s * direc[p, 0]
            y = origin[p, 1] + s * direc[p, 1]
            z = origin[p, 2] + s * direc[p, 2]
            if z - bilinear(dtm, dx0, dy0, dres, x, y) > htop + step:
                break
            s -= step
        s_start[p] = max(s, 0.0)
    return s_start


@njit(cache=True)
def assign_tiles(origin, direc, s_start, s_end, gx0, gy0, tile_size, ntx, nty):
    """CSR lists of pulses per tile (a pulse goes to every tile its xy bounding box touches)."""
    n = s_end.size
    lo_x = np.empty(n, np.int64)
    hi_x = np.empty(n, np.int64)
    lo_y = np.empty(n, np.int64)
    hi_y = np.empty(n, np.int64)
    counts = np.zeros(ntx * nty + 1, np.int64)
    for p in range(n):
        xa = origin[p, 0] + s_start[p] * direc[p, 0]
        xb = origin[p, 0] + s_end[p] * direc[p, 0]
        ya = origin[p, 1] + s_start[p] * direc[p, 1]
        yb = origin[p, 1] + s_end[p] * direc[p, 1]
        lo_x[p] = max(int(math.floor((min(xa, xb) - gx0) / tile_size)), 0)
        hi_x[p] = min(int(math.floor((max(xa, xb) - gx0) / tile_size)), ntx - 1)
        lo_y[p] = max(int(math.floor((min(ya, yb) - gy0) / tile_size)), 0)
        hi_y[p] = min(int(math.floor((max(ya, yb) - gy0) / tile_size)), nty - 1)
        for ty in range(lo_y[p], hi_y[p] + 1):
            for tx in range(lo_x[p], hi_x[p] + 1):
                counts[ty * ntx + tx + 1] += 1
    ptr = np.cumsum(counts)
    fill = ptr[:-1].copy()
    items = np.empty(ptr[-1], np.int64)
    for p in range(n):
        for ty in range(lo_y[p], hi_y[p] + 1):
            for tx in range(lo_x[p], hi_x[p] + 1):
                t = ty * ntx + tx
                items[fill[t]] = p
                fill[t] += 1
    return ptr, items


@njit(cache=True, parallel=True)
def accumulate_pulses(tile_ptr, tile_items, ntx, tile_vox, origin, direc, s_start, s_end,
                      echo_ptr, echo_s, echo_w, echo_canopy,
                      gx0, gy0, dx, h0, dz, dtm, dx0, dy0, dres, ds,
                      expo, hits, nin):
    """Beam-fraction weighted free path (expo), intercepted weight (hits) and entering pulse count.

    The beam fraction starts at 1 and drops by the echo weight at every echo. Tiles own disjoint
    voxel columns, so the parallel loop has no write conflicts.
    """
    ny, nx, nz = expo.shape
    htop = h0 + nz * dz
    ntiles = tile_ptr.size - 1
    for t in prange(ntiles):
        tix = t % ntx
        tiy = t // ntx
        ix0 = tix * tile_vox
        iy0 = tiy * tile_vox
        ix1 = min(ix0 + tile_vox, nx)
        iy1 = min(iy0 + tile_vox, ny)
        bx0 = gx0 + ix0 * dx
        bx1 = gx0 + ix1 * dx
        by0 = gy0 + iy0 * dx
        by1 = gy0 + iy1 * dx
        for q in range(tile_ptr[t], tile_ptr[t + 1]):
            p = tile_items[q]
            ox, oy, oz = origin[p, 0], origin[p, 1], origin[p, 2]
            ux, uy, uz = direc[p, 0], direc[p, 1], direc[p, 2]
            # clip the segment to the tile box in xy
            sa, sb = s_start[p], s_end[p]
            if abs(ux) > 1e-12:
                s1 = (bx0 - ox) / ux
                s2 = (bx1 - ox) / ux
                sa = max(sa, min(s1, s2))
                sb = min(sb, max(s1, s2))
            elif not (bx0 <= ox < bx1):
                sb = sa - 1.0
            if abs(uy) > 1e-12:
                s1 = (by0 - oy) / uy
                s2 = (by1 - oy) / uy
                sa = max(sa, min(s1, s2))
                sb = min(sb, max(s1, s2))
            elif not (by0 <= oy < by1):
                sb = sa - 1.0
            e = echo_ptr[p]
            e_end = echo_ptr[p + 1]
            f = 1.0
            while e < e_end and echo_s[e] <= sa:
                f -= echo_w[e]
                e += 1
            s = sa
            last = -1
            while s < sb and f > 1e-6:
                step = min(ds, sb - s)
                sm = s + 0.5 * step
                while e < e_end and echo_s[e] <= sm:
                    f -= echo_w[e]
                    e += 1
                if f <= 1e-6:
                    break
                xm = ox + sm * ux
                ym = oy + sm * uy
                ix = int((xm - gx0) / dx)
                iy = int((ym - gy0) / dx)
                if ix0 <= ix < ix1 and iy0 <= iy < iy1:
                    hag = oz + sm * uz - bilinear(dtm, dx0, dy0, dres, xm, ym)
                    if h0 <= hag < htop:
                        k = int((hag - h0) / dz)
                        expo[iy, ix, k] += f * step
                        v = (iy * nx + ix) * nz + k
                        if v != last:
                            nin[iy, ix, k] += 1
                            last = v
                s += step
            for e2 in range(echo_ptr[p], echo_ptr[p + 1]):
                if not echo_canopy[e2]:
                    continue
                xe = ox + echo_s[e2] * ux
                ye = oy + echo_s[e2] * uy
                ix = int((xe - gx0) / dx)
                iy = int((ye - gy0) / dx)
                if ix0 <= ix < ix1 and iy0 <= iy < iy1:
                    hag = oz + echo_s[e2] * uz - bilinear(dtm, dx0, dy0, dres, xe, ye)
                    if h0 <= hag < htop:
                        hits[iy, ix, int((hag - h0) / dz)] += echo_w[e2]
