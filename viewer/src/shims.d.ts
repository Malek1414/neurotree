declare module 'd3-force-3d' {
  type Accessor = (node: any) => number
  interface PositionForce { (alpha: number): void; strength(s: number | Accessor): PositionForce }
  export function forceX(x?: number | Accessor): PositionForce
  export function forceY(y?: number | Accessor): PositionForce
  export function forceZ(z?: number | Accessor): PositionForce
}
