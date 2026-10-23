import { expect, it } from 'vitest';
import { Sha256 } from './sha256';

// Fixed SHA-256 standard vectors, including padding across a block boundary.
it.each([
  ['', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
  ['abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
  [
    'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
    '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
  ],
  ['a'.repeat(1_000_000), 'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0'],
])('hashes a %i-byte vector in uneven slices', (input, expected) => {
  const hash = new Sha256();
  const bytes = new TextEncoder().encode(input);
  for (let offset = 0; offset < bytes.length; offset += 97)
    hash.update(bytes.subarray(offset, offset + 97));
  expect(hash.hex()).toBe(expected);
});
