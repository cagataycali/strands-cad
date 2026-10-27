// A small three.js STL viewer (lazy-loaded like expo's Twin): the model centred on a bed-sized grid, slow turntable.
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export default function StlView({ url, bed = [325, 320] }: { url: string; bed?: [number, number] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current!; let alive = true
    const dark = document.documentElement.getAttribute('data-scheme') === 'dark'
    const scene = new THREE.Scene()
    const cam = new THREE.PerspectiveCamera(40, 1, 1, 5000)
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); el.appendChild(renderer.domElement)
    const ctl = new OrbitControls(cam, renderer.domElement); ctl.enableDamping = true; ctl.autoRotate = true; ctl.autoRotateSpeed = 1.2
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.1))
    const dl = new THREE.DirectionalLight(0xffffff, 1.2); dl.position.set(1, 2, 1.5); scene.add(dl)
    const grid = new THREE.GridHelper(Math.max(...bed), 13, dark ? 0x46505c : 0xb6b6b6, dark ? 0x303740 : 0xd9d9d9); grid.rotation.x = Math.PI / 2; scene.add(grid)
    const mat = new THREE.MeshStandardMaterial({ color: dark ? 0x00cc60 : 0x007a3d, metalness: 0.05, roughness: 0.6 })
    const resize = () => { const w = el.clientWidth || 300, h = el.clientHeight || 220; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix() }
    resize(); const ro = new ResizeObserver(resize); ro.observe(el)
    new STLLoader().load(url, (geo) => {
      if (!alive) return
      geo.computeBoundingBox(); const bb = geo.boundingBox!; const size = new THREE.Vector3(); bb.getSize(size); const c = new THREE.Vector3(); bb.getCenter(c)
      geo.translate(-c.x, -c.y, -bb.min.z); geo.computeVertexNormals()
      scene.add(new THREE.Mesh(geo, mat))
      const r = Math.max(size.x, size.y, size.z) || 10
      cam.up.set(0, 0, 1); cam.position.set(r * 1.6, -r * 1.9, r * 1.3); ctl.target.set(0, 0, size.z / 2); ctl.update()
    }, undefined, () => { /* the drawer shows meta anyway */ })
    let raf = 0; const loop = () => { if (!alive) return; ctl.update(); renderer.render(scene, cam); raf = requestAnimationFrame(loop) }; loop()
    return () => { alive = false; cancelAnimationFrame(raf); ro.disconnect(); ctl.dispose(); renderer.dispose(); el.innerHTML = '' }
  }, [url, bed[0], bed[1]])
  return <div className="stl-view" ref={ref} aria-label="3D preview" />
}
