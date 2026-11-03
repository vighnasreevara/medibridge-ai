import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { STATUS, statusDef } from '@/domain/status';

import { SourceBadge, StatusBadge } from './common';

describe('status vocabulary', () => {
  it('every status has a label, a role and a glyph (never colour alone)', () => {
    for (const [key, def] of Object.entries(STATUS)) {
      expect(def.label, key).toBeTruthy();
      expect(def.glyph, key).toBeTruthy();
      expect(def.role, key).toBeTruthy();
    }
  });
  it('unknown statuses degrade to a readable label', () => {
    expect(statusDef('some_new_state').label).toBe('some new state');
  });
});

describe('StatusBadge', () => {
  it('renders text plus a glyph with an explanatory title', () => {
    const { container } = render(<StatusBadge status="waiting_human" />);
    expect(screen.getByText('Needs a person')).toBeInTheDocument();
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.firstElementChild?.getAttribute('title')).toMatch(/human approval gate/);
  });
});

describe('SourceBadge', () => {
  it('shows provenance and uncertainty explicitly', () => {
    render(<SourceBadge kind="patient_reported" certainty="uncertain" />);
    expect(screen.getByText('Patient-reported')).toBeInTheDocument();
    expect(screen.getByText('Uncertain')).toBeInTheDocument();
  });
});
