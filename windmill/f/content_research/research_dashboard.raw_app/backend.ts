// Keep the Windmill virtual backend bridge in one module. Importing the
// virtual `wmill` module through several relative paths makes the Full-code
// App bundler emit duplicate message listeners; every listener except the one
// that owns a request then reports a misleading "No job found" error.
export { backend } from './wmill'
