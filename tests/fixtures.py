"""Minimal HTML fixtures for list-page parsing (mirrors live HZZ markup)."""

LIST_PAGE_HTML = """
<html><body>
<form method="post" action="./Posloprimac_RadnaMjesta.aspx">
  <input type="hidden" name="__VIEWSTATE" value="abc" />
  <input type="submit" name="ctl00$MainContent$btnTrazilica" value="Povratak na tražilicu" />
  <select name="ctl00$MainContent$ddlPageSize" id="ctl00_MainContent_ddlPageSize">
    <option value="10">10</option>
    <option selected="selected" value="75">75</option>
  </select>
  <table id="ctl00_MainContent_gwSearch">
    <tr>
      <td>
        <a class="TitleLink" href="RadnoMjesto_Ispis.aspx?WebSifra=165734230">INZENJER BIOMEDICINE</a>
        <br/>Mjesto rada:
        <span id="ctl00_MainContent_gwSearch_ctl02_MjeNazivLabel">ZAGREB</span>
        <br/>Poslodavac:
        <span id="ctl00_MainContent_gwSearch_ctl02_PosNazivLabel">Dominus</span>
        <br/>Rok za prijavu:
        <span id="ctl00_MainContent_gwSearch_ctl02_RadMjeRokPrijaveLabel">21.9.2026.</span>
      </td>
    </tr>
    <tr>
      <td>
        <a class="TitleLink" href="RadnoMjesto_Ispis.aspx?WebSifra=165000001">KOZMETICAR</a>
        <br/>Mjesto rada:
        <span id="ctl00_MainContent_gwSearch_ctl03_MjeNazivLabel">SESVETE</span>
        <br/>Poslodavac:
        <span id="ctl00_MainContent_gwSearch_ctl03_PosNazivLabel">Salon</span>
        <br/>Rok za prijavu:
        <span id="ctl00_MainContent_gwSearch_ctl03_RadMjeRokPrijaveLabel">do popune</span>
      </td>
    </tr>
    <tr>
      <td>
        <ul class="pagination">
          <li class="active"><a href="javascript:__doPostBack('ctl00$MainContent$gwSearch$ctl13$ctl01','')" title="Idi na stranicu 1">1</a></li>
          <li><a href="javascript:__doPostBack('ctl00$MainContent$gwSearch$ctl13$ctl04','')" title="Idi na stranicu 2">2</a></li>
        </ul>
      </td>
    </tr>
  </table>
</form>
</body></html>
"""

BROWSE_PAGE_HTML = """
<html><body>
<form method="post">
  <input type="hidden" name="__VIEWSTATE" value="xyz" />
  <span class="RadioButtonList" id="ctl00_MainContent_rblZupanija">
    <input id="ctl00_MainContent_rblZupanija_0" name="ctl00$MainContent$rblZupanija" type="radio" value="" checked="checked" />
    <label for="ctl00_MainContent_rblZupanija_0">SVE ŽUPANIJE</label>
    <input id="ctl00_MainContent_rblZupanija_4" name="ctl00$MainContent$rblZupanija" type="radio" value="4"
           onclick="javascript:__doPostBack('ctl00$MainContent$rblZupanija$4','')" />
    <label for="ctl00_MainContent_rblZupanija_4">GRAD ZAGREB</label>
    <input id="ctl00_MainContent_rblZupanija_21" name="ctl00$MainContent$rblZupanija" type="radio" value="21" />
    <label for="ctl00_MainContent_rblZupanija_21">ZAGREBAČKA</label>
  </span>
  <a href="javascript:__doPostBack('ctl00$MainContent$DataList1$ctl00$lnkKategorija','')">Informatički stručnjaci 15</a>
  <a href="javascript:__doPostBack('ctl00$MainContent$DataList1$ctl05$lnkKategorija','')">Ugostitelji 0</a>
  <a href="javascript:__doPostBack('ctl00$MainContent$DataList1$ctl16$lnkKategorija','')">Zdravstvo 230</a>
</form>
</body></html>
"""
