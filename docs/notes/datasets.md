# Test datasets with defects (M2 Part 4)

Researched and downloaded 2026-10-14. Everything lives in `data/samples/<short-name>/` (gitignored, never committed).
Licences were read from the host's own record (Commons API, Zenodo API, figshare API), not from memory.

## 1. Footage for reconstruction

| name | what it shows | source | author | licence | attribution text | local path | size | frames | suitability |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| leipzig-wahren-depot | FPV drone flight over and through an abandoned railway depot hall (Leipzig-Wahren): rusty steel roof trusses and columns, flaking paint, graffiti | [Commons file](https://commons.wikimedia.org/wiki/File:Aerial_imagery_of_abandoned_industrial_site_next_to_the_intermodal_yard_of_Leipzig-Wahren_station,_Leipzig,_Germany.webm) | anotherview | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0) | "Aerial imagery of abandoned industrial site next to the intermodal yard of Leipzig-Wahren station, Leipzig, Germany" by anotherview, CC BY 3.0, via Wikimedia Commons | `data/samples/leipzig-wahren-depot/leipzig_wahren_depot.webm` | 428 MB | 217 s, 3840x2160, 29.97 fps, VP9 (about 6 500 frames) | Real rust on steel structure, but a fast wide-angle FPV flight through a dark hall: expect many registration breaks and weak scale. Use for segmenter tests on video and as a stress test. Subsample ~1 fps with short windows. |
| attisholz-areal | FPV + normal drone flight around a disused paper-mill site (Riedholz, CH): facades, water tower, chimney, concrete decay, graffiti | [Commons file](https://commons.wikimedia.org/wiki/File:Aerial_imagery_of_Attisholz_Areal_Riedholz_Switzerland.webm) | anotherview | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0) | "Aerial imagery of Attisholz Areal, Riedholz, Switzerland" by anotherview, CC BY 3.0, via Wikimedia Commons | `data/samples/attisholz-areal/attisholz_areal.webm` | 320 MB | 207 s, 3840x2160, 25 fps, VP9 (about 5 170 frames) | Best reconstruction candidate of the two: steadier stretches (last third: slower pass around the water tower and plant, good texture, overcast light, no sky blowout). Defects are mostly concrete staining/cracking and peeling plaster, little steel rust. Cut a 30-60 s segment around the tower. |
| raketa-shipwreck | Drone orbit of the rusted hull of the shipwreck "Raketa" on the beach at Kasispea, Estonia: rusty frames and plating, close passes and top-down views | [Commons file](https://commons.wikimedia.org/wiki/File:The_shipwreck_of_Raketa_on_the_shore_of_Kasispea_village_in_Estonia_(July_2022).webm) | Sillerkiil | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | "The shipwreck of Raketa on the shore of Kasispea village in Estonia (July 2022)" by Sillerkiil, CC BY-SA 4.0, via Wikimedia Commons | `data/samples/raketa-shipwreck/raketa_shipwreck.webm` | 75 MB | 104 s, 1920x1080 | **Best defect candidate** (found by the coordinator after Part T): slow orbit around one rusty object, good texture. Moving water around the hull will not match; sand and hull will. Share-alike applies to derived media (screenshots), not to our code. |

Honest assessment (Part T): no clip found shows a **slow orbit of a corroded tank or pipe**. Stock sites (Pexels, Pixabay) have such clips but their pages block scripted access (Pexels returns 403) and no clip was verified for length and motion, so none were downloaded. Commons video search returns almost no industrial-asset footage. A real oil-and-gas orbit video probably needs our own capture or a pilot customer.

Previously downloaded (M1, no defect focus): see `docs/notes/m1-results.md`.

## 2. Segmentation datasets (pixel masks)

| name | what it shows | source | author | licence | attribution text | local path | size | images | suitability |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ccs-corrosion | Corrosion Condition State dataset: steel bridge bearings and girders from VDOT inspection photos, pixel masks of corrosion | [Virginia Tech data (DOI 10.7294/16624663)](https://data.lib.vt.edu/articles/dataset/Corrosion_Condition_State_Semantic_Segmentation_Dataset/16624663) | Eric Bianchi, Matthew Hebdon (Virginia Tech) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (per figshare API record) | None required. The record asks for citing the dataset and the journal article (Bianchi and Hebdon). | `data/samples/ccs-corrosion/raw/` (`512x512/{Train,Test}/{images_512,mask_512}`, `original/{Train,Test}/{images,masks,json}`) | 699 MB | 440 (396 train / 44 test) | Best fit: real steel corrosion, in-the-wild inspection photos. Masks checked visually (overlays match rust). 512x512 masks are binary (0/128); `original/` and `json` hold the 4 condition states (good/fair/poor/severe). Dataset is bridges, not tanks. |
| ostrava-corrosion | Corrosion in Industrial Complexes in Ostrava: close-up DSLR photos of rusty objects on walls, floors, wood | [Zenodo 11235637](https://doi.org/10.5281/zenodo.11235637) | Matěj Frič (VSB - Technical University of Ostrava) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Frič, M. (2024). Corrosion in Industrial Complexes in Ostrava [Data set]. Zenodo. https://doi.org/10.5281/zenodo.11235637 (CC BY 4.0) | `data/samples/ostrava-corrosion/raw/corrosion-in-industrial-complexes-in-ostrava/{train,validation,test}/{images,masks}` | 740 MB | 105 (75/15/15) at 2048x2048 | Clean, accurate masks (checked visually; masks are RGB-coloured PNGs, background 0, corrosion colour 245/61). Mostly small objects, easy backgrounds: good for sanity checks, not representative of assets. |

Mask checks: three pairs per set were overlaid on the image and inspected; masks follow rust boundaries.

## 3. Needs owner decision — not downloaded

| item | reason |
| --- | --- |
| UMaine steel bridge Corrosion Condition Rating Database (Ameli and Landis, 514 images, pixel masks, ~6 GB) | Licence not verified: the repository page is gone (Digital Commons sunset) and the MDPI Infrastructures paper page blocked access. Likely CC BY but unconfirmed; check on MDPI/Specto. |
| DeepCrack | README restricts use to non-commercial research and education. |
| Özgenel concrete crack dataset (Mendeley, CC BY 4.0) | Licence is fine, but it is 227x227 classification patches with no masks, so not useful for segmentation tests. Not downloaded. |
| CrackForest, Crack500, NEU-DET | Not checked in time; licences from memory are unclear or research-only, so treat as excluded until read. |
| Roboflow Universe corrosion sets | Per-dataset licence, download needs a free account (needs login). Not tried. |
| Pexels / Pixabay stock clips (e.g. Pexels "Aerial view of Sloss Furnaces", "Ground storage tanks inside an industrial complex", "Aerial view of industrial ruins in Ontario") | Licences allow commercial use, but Pexels blocks scripted download and clips were not vetted for reconstructability. Needs a manual browser download if wanted. |
| Zenodo "Oil Tank Detection Dataset" (CC BY 4.0), 1.6 GB | Satellite images, wrong domain. |
