import React from 'react'

/** 34×20 pill toggle per the handoff spec. */
export default function Toggle({ on, onChange }) {
  return (
    <button
      className={'cr-toggle' + (on ? ' on' : '')} role="switch" aria-checked={on}
      onClick={(e) => { e.stopPropagation(); onChange(!on) }}
    >
      <i />
    </button>
  )
}
