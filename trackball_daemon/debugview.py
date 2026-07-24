# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Optional pygame cube window (--debug only) for verifying rotation 1:1.

Render-only: it reads the OutputEngine's view state and draws it. Keys mirror the old demo
(SPACE toggles mode, R recenters, ESC/close just closes this window -- the app keeps
running). Imported lazily so the app never needs pygame/PyOpenGL unless --debug is used.
"""
import threading

from .output import quat_to_gl_matrix

_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1),  (1, -1, 1),  (1, 1, 1),  (-1, 1, 1),
]
_FACES = [
    ((0, 1, 2, 3), (0.90, 0.20, 0.20)),
    ((4, 5, 6, 7), (0.20, 0.80, 0.30)),
    ((0, 4, 7, 3), (0.20, 0.40, 0.95)),
    ((1, 5, 6, 2), (0.95, 0.80, 0.20)),
    ((3, 2, 6, 7), (0.80, 0.30, 0.85)),
    ((0, 1, 5, 4), (0.20, 0.85, 0.90)),
]


def _run(engine, stop_event):
    import pygame
    from pygame.locals import DOUBLEBUF, OPENGL, QUIT, KEYDOWN, K_ESCAPE, K_SPACE, K_r
    from OpenGL.GL import (
        glClear, glClearColor, glEnable, glBegin, glEnd, glColor3f, glVertex3f,
        glLoadIdentity, glTranslatef, glMultMatrixf, glMatrixMode, glViewport,
        GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
        GL_QUADS, GL_PROJECTION, GL_MODELVIEW,
    )
    from OpenGL.GLU import gluPerspective

    pygame.init()
    pygame.display.set_mode((900, 700), DOUBLEBUF | OPENGL)
    pygame.display.set_caption("Trackball debug cube (verification)")
    glClearColor(0.08, 0.08, 0.10, 1.0)
    glEnable(GL_DEPTH_TEST)
    glViewport(0, 0, 900, 700)
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(45.0, 900 / 700.0, 0.1, 50.0)
    glMatrixMode(GL_MODELVIEW)

    clock = pygame.time.Clock()
    running = True
    while running and not stop_event.is_set():
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
            elif event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    running = False
                elif event.key == K_SPACE:
                    engine.toggle_mode()
                elif event.key == K_r:
                    engine.reset_view()

        q, pan_x, pan_y, dist = engine.get_view()
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -dist)
        glTranslatef(pan_x, pan_y, 0.0)
        glMultMatrixf(quat_to_gl_matrix(q))
        glBegin(GL_QUADS)
        for face, color in _FACES:
            glColor3f(*color)
            for idx in face:
                glVertex3f(*_VERTS[idx])
        glEnd()
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


def start_debug_view(engine, app, stop_event):
    t = threading.Thread(target=_run, args=(engine, stop_event), name="debugview", daemon=True)
    t.start()
    return t
