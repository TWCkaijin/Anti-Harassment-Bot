import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import AdminRerankTests from './AdminRerankTests';
import * as api from '../services/rerankTests';
import { behaviorConfig } from '../test/behaviorFixture';
vi.mock('../services/rerankTests', async importOriginal => ({ ...await importOriginal<typeof import('../services/rerankTests')>(), listDatasets: vi.fn(), listRuns: vi.fn(), getDataset: vi.fn(), saveCase: vi.fn(), getPreparation: vi.fn() }));
vi.mock('../services/api', async importOriginal => ({ ...await importOriginal<typeof import('../services/api')>(), getRerankModels: vi.fn().mockResolvedValue({ data: [] }) }));
const dataset: api.Dataset = { id: 'a'.repeat(32), name: 'Synthetic', archived: false, revision: 1, published_revision: null, case_count: 0, cases: [] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listDatasets).mockResolvedValue({ items: [dataset], next_cursor: null });
  vi.mocked(api.listRuns).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(api.getDataset).mockResolvedValue(dataset);
  vi.mocked(api.getPreparation).mockResolvedValue({ status: 'none', result: null });
  vi.mocked(api.saveCase).mockImplementation(async (_d, value) => ({ ...dataset, revision: 2, cases: [{ ...value, id: 'b'.repeat(32), query_sha256: 'hash' }] }));
});
it('requires local preview and fresh approval after edits, supports the 4000-character evaluation contract', async () => {
  render(<AdminRerankTests config={behaviorConfig} csrfToken="csrf" active />);
  await screen.findByRole('option', { name: 'Synthetic' });
  fireEvent.change(screen.getByLabelText('資料集', { exact: true }), { target: { value: dataset.id } });
  const input = await screen.findByLabelText('查詢原文（只留在本機，最多 4,000 字）');
  fireEvent.change(input, { target: { value: '聯絡 private@example.com ' + '甲'.repeat(2500) } });
  expect(screen.getByRole('button', { name: '儲存題目與標註' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '本機去識別預覽' }));
  fireEvent.click(screen.getByLabelText('我已確認此去識別文字可儲存及送出'));
  fireEvent.click(screen.getByRole('button', { name: '儲存題目與標註' }));
  await waitFor(() => expect(api.saveCase).toHaveBeenCalledTimes(1));
  const sent = vi.mocked(api.saveCase).mock.calls[0][1];
  expect(sent.query).not.toContain('private@example.com');
  expect(sent.query.length).toBeGreaterThan(2000);
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: '甲'.repeat(4001) } });
  expect(screen.getByRole('button', { name: '儲存題目與標註' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '本機去識別預覽' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('4,000');
  expect(api.saveCase).toHaveBeenCalledTimes(1);
});
it('blocks invalid fragment budgets and preserves the type-coverage constraint', () => {
  const settings = api.defaultSettings(behaviorConfig);
  expect(api.validateSettings({ ...settings, candidate_k: 2, top_k: 3 }, [['law']])).toContain('K');
  expect(api.validateSettings({ ...settings, top_k: 1 }, [['law', 'judgment']])).toContain('類型');
  expect(api.validateSettings({ ...settings, top_k: 1, preserve_data_types: false }, [['law', 'judgment']])).toBeNull();
});
it('does not load private data while locked', () => {
  render(<AdminRerankTests config={behaviorConfig} csrfToken="csrf" active={false} />);
  expect(api.listDatasets).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '建立資料集' })).toBeDisabled();
});
