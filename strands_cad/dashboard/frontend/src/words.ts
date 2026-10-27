// Tool calls in words: what the agent is DOING, not JSON. "design · bracket.scad", "slice · cube.3mf · PLA_0_20", "print → gate".
export type ToolWords = { verb: string; detail: string }
const base = (p: unknown) => String(p ?? '').split('/').pop() || ''

export function toolWords(name: string, input: unknown): ToolWords {
  const inp = (input && typeof input === 'object' ? input : {}) as Record<string, any>
  if (name === 'print_pipeline' || name === 'design_and_print') return { verb: 'pipeline', detail: inp.name || inp.title || base(inp.model || inp.stl_file) || 'design → slice → gate → print' }
  if (name.startsWith('scad_render') || name === 'cq_render_stl' || name === 'sdf_render_stl' || name.startsWith('neural_') || name.startsWith('mesh_from')) return { verb: 'design', detail: base(inp.output_stl || inp.output_png || inp.scad_file) }
  if (name === 'scad_view' || name === 'scad_turntable' || name === 'gcode_preview_png') return { verb: 'preview', detail: base(inp.scad_file || inp.gcode_file) }
  if (name === 'stl_printability' || name === 'stl_check_clearance' || name === 'gcode_check' || name === 'scad_validate') return { verb: 'check', detail: base(inp.stl_file || inp.gcode_file) }
  if (name.startsWith('stl_') || name.startsWith('mesh_')) return { verb: 'mesh', detail: `${name.replace(/^(stl|mesh)_/, '')} · ${base(inp.stl_file || inp.input_file)}` }
  if (name === 'slice_bambu') return { verb: 'slice', detail: `${base(inp.input_3mf)} · ${inp.profile || ''}` }
  if (name === 'slice_estimate') return { verb: 'estimate', detail: base(inp.gcode_file) }
  if (name.startsWith('mf3_') || name === 'plate_export_colored_3mf') return { verb: '3mf', detail: base(inp.output_3mf || inp.mf3_file) }
  if (name === 'bambu_send' || name === 'plate_slice_and_print') return { verb: 'print', detail: base(inp.file_path) }
  if (name === 'bambu_upload') return { verb: 'upload', detail: base(inp.file_path) }
  if (name === 'bambu_status' || name === 'bambu_ams' || name === 'printer_status') return { verb: 'printer', detail: name.replace('bambu_', '') }
  if (name === 'bambu_control') return { verb: 'control', detail: inp.action || '' }
  if (name === 'bambu_camera') return { verb: 'camera', detail: 'snapshot' }
  if (name.startsWith('plate_')) return { verb: 'plate', detail: name.replace('plate_', '') }
  if (name.startsWith('telegram_')) return { verb: 'telegram', detail: name.replace('telegram_', '') }
  return { verb: name, detail: '' }
}
export const humanise = (t: string) => t.replace(/\s+/g, ' ').slice(0, 240)
