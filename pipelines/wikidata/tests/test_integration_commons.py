import httpx
import pytest

from wikidata.silver import commons_image_metadata as cim
from wikidata.wikidata_client import USER_AGENT

# Hits the public Commons API: run by the Live contract workflow, never by PR CI. Assertions are invariants only,
# since the exact values are Commons-editable; the recorded real responses live in tests/silver/fixtures.
pytestmark = [pytest.mark.integration, pytest.mark.live]


def test_fetch_batch_reads_license_and_author_from_the_live_api() -> None:
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        [row] = cim._fetch_batch(client, ["RadioheadO2211125 composite.jpg"])

    assert row["thumb_url"].endswith("/500px-RadioheadO2211125_composite.jpg")
    assert "/archive/" not in row["thumb_url"]
    assert row["page_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert row["author"]
    assert row["license"]


# Real artist images with the longest percent-encoded names on Commons: together they overflow a GET URL (414).
LONG_NAMED_FILES = [
    "(싹튜브) 서종예 SAC스타특강 래퍼 아웃사이더 소통교감콘서트 서울종합예술실용학교 재학생특강 46s.jpg",
    "2020 год, Народный артист СССР Владислав Чернушенко на сцене СПб Капеллы.png",
    "2018.01.04 AKB48 Team TP 1st「勇往直前」專屬雙層觀光巴士亮相&快閃見面活動@西門町捷運6號出口前廣場 (46543972862).jpg",
    "160625 플레디스걸즈 콘서트 임나영 김민경 강경원 강예빈 배성연 김예원 박시연 카일라 직찍 8.jpg",
    "13.04.2019, СПб консерватория, пианист профессор Малов О.Ю. фрагмент фото.png",
    "170511 강남 보너스베이비 직캠 9 아프리카TV 우리끼리 같이 놀자 프릭업 스튜디오 삼성역 4m59s.jpg",
    "190521 빈지노X홍종현, 감탄나오는 멋짐 '바이레도(BYREDO)' 플래그십 스토어 오픈 기념행사 36s.jpg",
    "(TV10) 탁재훈 인생의 후회 아마 다 아실 것 너스레 (tvN 내게 남은 48시간 제작발표회) 1m33s.jpg",
    "161006 AMN 빅 콘서트 - 후쿠무라 미즈키 우타카다 새터데이나이트 직캠 by DaftTaengk.mp4 (Haruna Ogata) 2m49s.jpg",
    "161006 AMN 빅 콘서트 - 후쿠무라 미즈키 우타카다 새터데이나이트 직캠 (Mizuki Fukumura) by DaftTaengk 3m37s.jpg",
    "06.15 總統接見「美國智庫『保衛民主基金會』（FDD）軍事安全專家交流團」 (52976061215).jpg",
    "161006 AMN 빅 콘서트 - 후쿠무라 미즈키 우타카다 새터데이나이트 직캠 by DaftTaengk (Miki Nogaka) 1m36s.jpg",
    "0226 (여자)아이들 소연 포커스&세로캠, 2nd 미니앨범 'I made' 쇼케이스 포토타임 (derived).jpg",
    "161006 AMN 빅 콘서트 - 후쿠무라 미즈키 우타카다 새터데이나이트 직캠 by DaftTaengk (Sakura Oda) 12s.jpg",
    "2015-08-15. Александр Мирзаян на фестивале «Большой Донбасс» 003.jpg",
    "A portrait of Shri Bhalchandra Vyankatesh Pendharkar who will be presented with the Sangeet Natak Akademi Award for Natya Sangeet - Maharashtra by the President Dr. A.P.J Abdul Kalam in New Delhi on October 26, 2004.jpg",
    "(Portrait of Micky Folus, Joe Shulman, Claude Thornhill, Billy Exiner, and Barry Galbraith, Columbia Pictures studio, the making of Beautiful Doll, New York, N.Y., ca. Sept. 1947) (LOC) (5148195205).jpg",
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Ayumi Ishida) by DaftTaengk 48s (3).jpg",
    "190706 짝꿍 특집! 비와이X크러쉬, 스웩넘치는 브이 (KBS 해피투게더4 출근길) 1m 31s.jpg",
    "171209 桃乃木かな(모모노기 카나) 2017 FAN MEETING in SEOUL (First Kiss)2部 写真 ♡桃乃木かな♡ (3).jpg",
    "151002 부산국제영화제 핑크와그레이 GV 나카지마 유토, 스다 마사키 직찍 (12).jpg",
    "2015-08-15. Павел Усанов на фестивале «Большой Донбасс» 001 (cropped).jpg",
    "(15.07.18) 보령 도심속 썸머 페스티벌 직찍 ( 달샤벳 세리 아영 우희 가은 ) 03.jpg",
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Haruna Iikubo) by DaftTaengk 2m15s.jpg",
    "A portrait of Shri Prabhat Sarma who will be presented with the Sangeet Natak Akademi Award for Traditional & Folk Music - Assam by the President Dr. A.P.J Abdul Kalam in New Delhi on October 26, 2004.jpg",
    "A still of Shri Ghulam Mustafa Waris Khan who will be presented with the Sangeet Natak Akademi Award for Hindustani Music - Vocal by the President Dr. A.P.J Abdul Kalam in New Delhi on October 26, 2004.jpg",
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Erina Ikuta) by DaftTaengk 3m31s.jpg",
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Masaki Sato) by DaftTaengk 8m14s.jpg",
    '1924-11-04, El Imparcial, Gente conocida, El maestro Alonso y D. Manuel Fernández de la Puente, autores de la zarzuela "El molino de la viuda", que se estrena esta noche, Pellicer (cropped).jpg',
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Akane Haga) by DaftTaengk 4m50s.jpg",
    "01.01 中華民國106年元旦總統府升旗音樂會中,DJ樂手李英宏於DJ台上唱歌 (31173967134).jpg",
    "151016 걸스데이 소진 - KBS 뮤직뱅크 인천공항 스카이 페스티벌 공개방송 (2).jpg",
    "161006 AMN 빅 콘서트 - 마키노 마리아 모닝구무스메 메들리 직캠 (Haruka Kudo) by DaftTaengk 48s.jpg",
    "161006 AMN 빅 콘서트 - 모닝구무스메 16 우타카타 새터데이나이트 직캠 by DaftTaengk 3m50s.jpg",
    "2019.01.21 AKB48 Team 8 x TICA台北國際動漫節 抵達台灣@台北國際松山機場 (39857863953) 佐藤 朱.jpg",
    "15.05.27 원주 위문열차 직찍( 피에스타 재이, 린지, 예지, 혜미, 차오루 ) 03.jpg",
    "160618 플레디스걸즈 콘서트 강경원 김민경 김예원 배성연 카일라 직찍 3.jpg",
    "160618 플레디스걸즈 콘서트 강경원 김민경 김예원 배성연 카일라 직찍 6.jpg",
    "160702 플레디스걸즈 콘서트 김민경 강경원 김예원 배성연 카일라 직찍 2.jpg",
    "2019.01.26「第14回 KKBOX MUSIC AWARDS in Taiwan」乃木坂46 抵達台灣@台北國際松山機場 (39913526853) (cropped).jpg",
    "(Portrait of John Chance, Paul Kashian, Nick Travis, Chuck Genduso, Joe Ferrante, and Curly Broyles, Hotel Commodore, Century Room, New York, N.Y., ca. Jan. 1947) (LOC) (5395851710).jpg",
    "(피키라이브) 장기하와 얼굴들 - 내 사랑에 노련한 사람이 어딨나요.jpg",
    "2015. Фестиваль «Большой Донбасс» 1400 (Алексей Иовчев).jpg",
    "1929-10-28, La Voz, El compositor mejicano folklorista Ignacio Fernández Esperón, la señorita Elisa Cavalcanti y Rubén S. Orcillo, Sancha (cropped) Ignacio Fernández Esperón.jpg",
    "【二代目ファンキル先生 -39】みかこし登場！一周年特別企画② 【小松未可子】 16m09s 優木かな.jpg",
    "Первое исполнение сочинения Луи Андриссена La passion в Санкт-Петербурге..jpg",
]
EMPTY_EXTMETADATA_FILE = "John Nolan.jpg"
NORMALIZED_AND_REDIRECTED_FILE = "The_Beatles_1964.jpg"
MISSING_FILE = "TheMusicTree integration test file that does not exist.jpg"


def test_fetch_batch_reads_a_full_batch_of_awkward_files_from_the_live_api() -> None:
    image_files = [
        *LONG_NAMED_FILES,
        "RadioheadO2211125_composite.jpg",
        EMPTY_EXTMETADATA_FILE,
        NORMALIZED_AND_REDIRECTED_FILE,
        MISSING_FILE,
    ]
    assert len(set(image_files)) == cim.BATCH_SIZE

    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        rows = cim._fetch_batch(client, image_files)

    assert [row["image_file"] for row in rows] == image_files
    for row in rows:
        if row["image_file"] == MISSING_FILE:
            assert row == {"image_file": MISSING_FILE}
        else:
            assert row["thumb_url"].startswith("https://")
            assert row["page_url"].startswith("https://commons.wikimedia.org/wiki/File:")
